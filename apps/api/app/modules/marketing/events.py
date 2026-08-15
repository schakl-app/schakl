"""What ``marketing`` does when another module writes a fact it also holds (#338).

One event today: ``google_ads.account.attached``. The ``google_ads`` module owns *which Ads
customer a client is*; this module holds the same sentence a second time, as a
``marketing_links`` row, because a link is what the panel, the tab and the cross-client
overview are built out of. ``MarketingService._attach_ads_account`` has always mirrored a
marketing link **into** that module. This is the missing return leg.

Without it the two write paths disagreed in a way no screen could explain. Linking an account
through Instellingen → Google Ads wrote one row, so the client's page listed the Ads account in
the Google Ads panel while the marketing panel directly above it went on saying *"koppel een
Google-account om Analytics, Search Console en Ads van deze klant te tonen"*, and ``/marketing``
listed the client as having no source at all. The Ads *was* connected; two screens said it was
not, and the cure — do it a second time, in the other panel — was discoverable from nothing.

Three properties keep it safe:

* **It is a side effect of a write the caller was already allowed to make**, so it asks for no
  permission of its own — the rule §16 states for the activity trail. The caller held
  ``google_ads.settings.manage``; demanding ``marketing.link.manage`` on top would refuse the
  mirror for exactly the admin who is allowed to create the thing being mirrored.
* **It writes the row directly rather than calling ``create_link``**, which would re-enter
  ``_attach_ads_account`` → ``attach`` → this handler. The bounce would terminate (the upsert
  finds its own row the second time), but a write path that recurses through two modules to
  settle is a thing nobody should have to reason about twice.
* **It respects the licence.** A mirror is still a write into ``marketing``, and an expired
  module goes read-only rather than half-writable (epic #140). Skipping leaves exactly the
  behaviour of every release before this one, on an instance whose marketing screens are locked
  anyway — so nothing visible disagrees.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select

from app.core.entitlements.service import OrgPlan, sku_writable
from app.core.events import EmitContext
from app.core.googleads import normalise_customer_id
from app.core.jobs import enqueue
from app.modules.marketing.models import MarketingLink, MarketingSource

logger = logging.getLogger(__name__)


async def on_google_ads_account_attached(ctx: EmitContext, payload: dict[str, Any]) -> None:
    """Record the ``gads`` marketing link for an Ads account the other module just attached."""
    company_id = payload.get("company_id")
    customer_id = str(payload.get("customer_id") or "")
    if not isinstance(company_id, uuid.UUID) or not customer_id:
        return
    if not await sku_writable("marketing", plan=OrgPlan.of(ctx.org)):
        return

    account_id = payload.get("account_id")
    # Matched on the **normalised** id, never on the string. `external_id` is marketing's own
    # display copy and it holds whatever the caller stored — the picker writes a bare id, a
    # hand-typed link writes `124-264-3293`, a GAQL row writes `customers/1242643293` — while
    # `google_ads_accounts.customer_id` is normalised on the way in. Comparing the raw text
    # would miss the link that already exists and mint a second one for the same account, which
    # is the exact duplicate this mirror exists to prevent. The account id is preferred where a
    # previous pass already stamped it: that column is the authority (§6's seam), the text is not.
    candidates = (
        await ctx.session.scalars(
            select(MarketingLink).where(
                MarketingLink.org_id == ctx.org.id,
                MarketingLink.company_id == company_id,
                MarketingLink.source == MarketingSource.GADS.value,
            )
        )
    ).all()
    existing = next(
        (
            row
            for row in candidates
            if account_id is not None and row.google_ads_account_id == account_id
        ),
        None,
    ) or next(
        (row for row in candidates if normalise_customer_id(row.external_id) == customer_id),
        None,
    )
    display_name = str(payload.get("descriptive_name") or "") or customer_id
    # The two keys marketing's own Ads adapter reads back off a link: the currency it labels
    # amounts with, and the manager the account must be reached through. `manager_id` is the
    # load-bearing half — without it every later call on a child account is made by a login
    # that has no direct grant on it, and 403s.
    config = {
        "currency": payload.get("currency_code"),
        "manager_id": payload.get("login_customer_id"),
    }
    config = {key: value for key, value in config.items() if value}

    if existing is not None:
        # An unlinked-then-relinked account reactivates its own row rather than minting a
        # second one, exactly as `create_link` does: the synced history is attributable to it.
        existing.active = True
        existing.display_name = display_name
        # Merged, never replaced: a link created through the marketing picker may already carry
        # keys this payload knows nothing about, and a mirror must not blank them.
        existing.config = {**existing.config, **config}
        if payload.get("connection_id") is not None:
            existing.connection_id = payload["connection_id"]
        if isinstance(account_id, uuid.UUID):
            existing.google_ads_account_id = account_id
        await ctx.session.flush()
        return

    link = MarketingLink(
        org_id=ctx.org.id,
        company_id=company_id,
        source=MarketingSource.GADS.value,
        external_id=customer_id,
        display_name=display_name,
        config=config,
        connection_id=payload.get("connection_id"),
        google_ads_account_id=account_id if isinstance(account_id, uuid.UUID) else None,
        created_by_user_id=getattr(ctx.user, "id", None),
    )
    ctx.session.add(link)
    await ctx.session.flush()

    # Thirteen months of daily aggregates, so the sparkline and the year-over-year delta work
    # the day after linking rather than a year after — the same job `create_link` queues, and
    # deferred for the same reason: this transaction has to commit before the job reads the row.
    # A queue miss is not fatal (the nightly sync catches up), so it is logged, never raised:
    # this handler runs inside somebody else's write, and failing it would roll back the Ads
    # account the user actually asked for.
    try:
        await enqueue(
            "marketing_backfill_link",
            str(ctx.org.id),
            str(link.id),
            _defer_by=5,
            _job_id=f"marketing-backfill-{link.id}",
        )
    except Exception:  # noqa: BLE001 — a nicety this write rides on, never its purpose
        logger.warning("could not enqueue marketing backfill for mirrored link %s", link.id)
