"""Linking containers, and reading what is in them. Business-licensed — see LICENSE.

The read half of the integration: the org's posture, the container rows, the picker that finds
them, the probe that says whether they still answer, and the live reads a screen or an agent makes.

Three rules from the rest of the codebase are load-bearing here and easy to lose:

* **Every in-request Google call happens inside ``ctx.release_db()``** (docs/PERFORMANCE.md). A
  request runs as one transaction pinning one pool connection; held across a call that may take
  thirty seconds, a handful of these drain the pool and every other request queues until
  ``pool_timeout``, which reads as the whole site freezing. Enter the client *first* — it reads
  settings — then release.
* **A probe fails softly and success clears the flag** (CLAUDE.md §10, learned from Cloudflare).
  ``verify`` records what it found and never raises for a partial answer, and a success wipes
  ``status``/``last_error`` — a flag that only ever turns on leaves a red line on a row nothing is
  wrong with, through every sync that works afterwards.
* **Absence is a stated state, never a guess.** A container that has never been published has no
  live version, which is a real and common condition (a container somebody created last week);
  GTM answers 404 for it, and reading that as "the container is gone" would flag a healthy row.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from app.core.activity import ActivityService
from app.core.activity.service import snapshot
from app.errors import AppError
from app.integrations.google import client as google_client
from app.integrations.google.models import ConnectionStatus, GoogleConnection
from app.integrations.google.oauth import has_tag_manager_read_scope
from app.integrations.google_tag_manager.client import GtmClient, gtm_client
from app.integrations.google_tag_manager.errors import (
    GtmError,
    GtmNotConfigured,
    GtmNotFoundError,
    GtmQuotaError,
    describe_failure,
)
from app.integrations.google_tag_manager.models import (
    GtmContainer,
    GtmContainerStatus,
    GtmConversion,
    GtmConversionStatus,
    GtmSettings,
)

#: How many GTM accounts one connection contributes to the picker. An agency holds one or two; a
#: reseller can hold dozens, and an unbounded read behind a combobox is how a picker becomes a
#: timeout (§9). Over the cap is **reported**, never silently dropped.
MAX_PICKER_ACCOUNTS = 50
#: And the containers under them, for the same reason one level down.
MAX_PICKER_CONTAINERS = 500
#: **How many accounts one search may open, and the number this whole surface turns on.**
#:
#: Tag Manager's quota is *per user per minute* and it is small — a first live run against an
#: agency holding **44** accounts refused on the 45th request with ``RESOURCE_EXHAUSTED``, because
#: "list every account, then list every account's containers" is ``1 + n`` calls and n is not
#: ours to choose. So the picker asks Google for the account list (one call, always affordable)
#: and opens only the accounts a *search* selected. Eight is what keeps the worst case at nine
#: requests, comfortably inside the minute, while still filling the box for the agency with two.
MAX_SEARCH_ACCOUNTS = 8
#: How many workspaces one observe walks for staged-change counts. A container has one or two;
#: a container with twenty has a process problem this number is not going to fix.
MAX_OBSERVED_WORKSPACES = 5

_ENTITY = "gtm_container"
_SETTINGS_ENTITY = "gtm_settings"

#: What an edit records. Only the fields schakl decides — what Google said is refreshed, not
#: edited, and an activity line per nightly rename would bury the decisions in observations.
_TRACKED = ("company_id", "website_id", "active", "connection_id", "summary", "goal")


def container_url(account_id: str, container_id: str) -> str:
    """A deep link into Tag Manager itself.

    Built in one place so the panel's ⧉, the detail screen and an MCP answer cannot disagree —
    and built rather than stored, because GTM's own ``tagManagerUrl`` names a *workspace* that
    may not exist tomorrow.
    """
    return (
        "https://tagmanager.google.com/#/container/"
        f"accounts/{account_id}/containers/{container_id}/workspaces"
    )


@dataclass(frozen=True)
class AvailableContainer:
    """One pickable container, as the live picker offers it."""

    account_id: str
    account_name: str
    container_id: str
    public_id: str
    name: str
    path: str
    usage_context: tuple[str, ...]
    already_linked: bool = False


@dataclass(frozen=True)
class PickerResult:
    containers: list[AvailableContainer]
    warnings: tuple[str, ...] = ()
    #: Every Tag Manager account this grant reaches, and how many of them were actually opened.
    #: Both travel because a search that shows eight of forty-four must be able to *say so*: a
    #: short list that looks complete is the failure §17 names, and "8 van 44" is the only thing
    #: that turns an empty result into an instruction (#373's long-tail rule).
    accounts_total: int = 0
    accounts_read: int = 0


#: What a Tag Manager public id looks like. Deliberately generous about length and case — the
#: point is to tell "the user pasted the id off the client's website" apart from "the user is
#: typing a name", not to validate: an id that is not a container comes back 404 from Google,
#: which is a better answer than a regex's opinion.
_PUBLIC_ID_RE = re.compile(r"^GTM-[A-Z0-9]{4,12}$", re.IGNORECASE)


def _matches_account(account: dict[str, Any], needle: str) -> bool:
    """Whether a search opens this account.

    **The account name is the only thing a search can match before spending a request**, and at an
    agency it is already the client's name ("Briellaerd", "campings Zeeland"). Matching a container
    name would mean listing every container first, which is the cost this whole shape exists to
    avoid — so the box says what it searches, and the id path above covers the other way anybody
    identifies a container. An empty needle matches everything, which is what makes a blank box
    show the first few accounts instead of nothing.
    """
    if not needle:
        return True
    folded = needle.casefold()
    return folded in str(account.get("name") or "").casefold() or folded == str(
        account.get("accountId") or ""
    )


def _option(
    container: dict[str, Any], account: dict[str, Any], linked: set[str]
) -> AvailableContainer | None:
    """One Google container payload as the picker offers it, or ``None`` if it names no id.

    The account is passed in rather than read off the container because ``containers:lookup``
    answers a container and no account name; falling back to the numeric id is what keeps a
    looked-up container from rendering under a blank heading.
    """
    container_id = str(container.get("containerId") or "")
    if not container_id:
        return None
    account_id = str(container.get("accountId") or account.get("accountId") or "")
    return AvailableContainer(
        account_id=account_id,
        account_name=str(account.get("name") or account_id),
        container_id=container_id,
        public_id=str(container.get("publicId") or ""),
        name=str(container.get("name") or container_id),
        path=str(container.get("path") or ""),
        usage_context=tuple(str(v) for v in (container.get("usageContext") or [])),
        already_linked=container_id in linked,
    )


class GtmService:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.activity = ActivityService(ctx)

    # --- settings ---------------------------------------------------------------------------- #

    async def settings_row(self, *, create: bool = False) -> GtmSettings | None:
        row = await self.ctx.session.scalar(
            select(GtmSettings).where(GtmSettings.org_id == self.ctx.org.id)
        )
        if row is None and create:
            row = GtmSettings(org_id=self.ctx.org.id)
            self.ctx.session.add(row)
            await self.ctx.session.flush()
        return row

    async def save_settings(
        self,
        *,
        writes_enabled: bool | None = None,
        own_workspace: bool | None = None,
        workspace_name: str | None = None,
    ) -> GtmSettings:
        self.ctx.require("google_tag_manager.settings.manage")
        row = await self.settings_row(create=True)
        assert row is not None
        changed: list[str] = []
        if writes_enabled is not None and writes_enabled != row.writes_enabled:
            row.writes_enabled = writes_enabled
            changed.append("writes_enabled")
        if own_workspace is not None and own_workspace != row.own_workspace:
            row.own_workspace = own_workspace
            changed.append("own_workspace")
        if workspace_name is not None:
            name = workspace_name.strip() or "schakl"
            if name != row.workspace_name:
                row.workspace_name = name
                changed.append("workspace_name")
        if changed:
            await self.activity.record(_SETTINGS_ENTITY, row.id, "updated", {"changed": changed})
        return row

    async def require_writes_enabled(self) -> None:
        """The instance-wide kill switch, checked before any mutating GTM call.

        Separate from the permissions and checked separately: the permission decides *who*, this
        decides *whether*. An owner who has just watched something surprising appear on a client's
        website needs one switch that stops all of it without editing four role grants.
        """
        row = await self.settings_row()
        if row is not None and not row.writes_enabled:
            raise AppError("gtm_writes_disabled", "errors.gtm_writes_disabled", status_code=409)

    # --- containers -------------------------------------------------------------------------- #

    async def list_containers(
        self, *, company_id: uuid.UUID | None = None, active_only: bool = False
    ) -> list[GtmContainer]:
        """Every linked container this caller may see.

        Rides ``scoped_select()`` rather than a hand-built ``where(org_id == …)``: this is a
        parameterless list returning ``name``, which for a client container *is* the client's
        name — exactly the shape ``test_company_groups``' sweep hunts for.
        """
        stmt = self.ctx.repo(GtmContainer).scoped_select()
        if company_id is not None:
            stmt = stmt.where(GtmContainer.company_id == company_id)
        if active_only:
            stmt = stmt.where(GtmContainer.active.is_(True))
        stmt = stmt.order_by(GtmContainer.name, GtmContainer.public_id)
        return list((await self.ctx.session.scalars(stmt)).all())

    async def get_container(self, container_pk: uuid.UUID) -> GtmContainer:
        """One container, 404 outside this caller's tenant or company horizon (§15).

        404 rather than 403: a container the caller may not see must not be revealed to exist by
        the difference between two status codes.
        """
        row = await self.ctx.session.scalar(
            self.ctx.repo(GtmContainer).scoped_select().where(GtmContainer.id == container_pk)
        )
        if row is None:
            raise AppError("not_found", "errors.not_found", status_code=404)
        return row

    async def link(
        self,
        *,
        account_id: str | None = None,
        container_id: str | None = None,
        public_id: str | None = None,
        company_id: uuid.UUID | None = None,
        website_id: uuid.UUID | None = None,
    ) -> GtmContainer:
        """Attach a container to this org, idempotent on ``(org_id, container_id)``.

        Named either way round: the numeric pair Google addresses it by, or the ``GTM-XXXXXXX``
        that is on the client's website. The second is resolved through ``containers:lookup``,
        which is a live call — and worth it, because the alternative is asking somebody to find a
        numeric container id in a URL before they can link the container they are looking at.

        Idempotent because a second link of the same container is somebody re-attaching it to the
        right client, not an error to shout about — and because the unique constraint would
        otherwise surface as a 500.
        """
        self.ctx.require("google_tag_manager.settings.manage")
        connection = await self._own_connection()

        resolved_account = (account_id or "").strip()
        resolved_container = (container_id or "").strip()
        looked_up: dict[str, Any] | None = None
        if not (resolved_account and resolved_container):
            tag_id = (public_id or "").strip().upper()
            if not tag_id:
                raise AppError(
                    "validation",
                    "errors.validation",
                    status_code=422,
                    fields={"public_id": "errors.gtm_container_not_named"},
                )
            looked_up = await self._lookup(connection, tag_id)
            resolved_account = str(looked_up.get("accountId") or "")
            resolved_container = str(looked_up.get("containerId") or "")
        if not (resolved_account and resolved_container):
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"public_id": "errors.gtm_container_not_found"},
            )

        repo = self.ctx.repo(GtmContainer)
        existing = await self.ctx.session.scalar(
            repo.scoped_select().where(GtmContainer.container_id == resolved_container)
        )
        if existing is not None:
            before = snapshot(existing, _TRACKED)
            if company_id is not None:
                repo._guard_company_write({"company_id": company_id})
                existing.company_id = company_id
            if website_id is not None:
                existing.website_id = website_id
            existing.connection_id = connection.id
            if not existing.active:
                existing.active = True
            await self.ctx.session.flush()
            await self.activity.record_update(
                _ENTITY, existing.id, before, snapshot(existing, _TRACKED)
            )
            if looked_up is not None:
                self._apply_container_payload(existing, looked_up)
            return existing

        repo._guard_company_write({"company_id": company_id})
        row = GtmContainer(
            org_id=self.ctx.org.id,
            account_id=resolved_account,
            container_id=resolved_container,
            public_id=(public_id or "").strip().upper(),
            path=f"accounts/{resolved_account}/containers/{resolved_container}",
            company_id=company_id,
            website_id=website_id,
            connection_id=connection.id,
        )
        if looked_up is not None:
            self._apply_container_payload(row, looked_up)
        self.ctx.session.add(row)
        await self.ctx.session.flush()
        await self.activity.record_created(
            _ENTITY,
            row.id,
            {"container_id": resolved_container, "company_id": str(company_id or "")},
        )
        return row

    async def update_container(
        self,
        row: GtmContainer,
        *,
        company_id: uuid.UUID | None = None,
        website_id: uuid.UUID | None = None,
        active: bool | None = None,
        summary: str | None = None,
        goal: str | None = None,
        company_id_set: bool = False,
        website_id_set: bool = False,
        summary_set: bool = False,
        goal_set: bool = False,
    ) -> GtmContainer:
        """Edit the fields schakl *decided*. What Google said is refreshed by verify, never typed.

        The ``*_set`` flags carry the absent-vs-null distinction the payload alone cannot
        (CLAUDE.md §18): omitted means leave it alone, an explicit ``null`` detaches — which is a
        real state (the agency's own container), not an accident. The two prose fields store
        ``""`` for "nothing written" (the Ads policy shape), so their explicit ``null`` clears.
        """
        self.ctx.require("google_tag_manager.settings.manage")
        before = snapshot(row, _TRACKED)
        if company_id_set:
            self.ctx.repo(GtmContainer)._guard_company_write({"company_id": company_id})
            row.company_id = company_id
        if website_id_set:
            row.website_id = website_id
        if active is not None:
            row.active = active
        if summary_set:
            row.summary = (summary or "").strip()
        if goal_set:
            row.goal = (goal or "").strip()
        await self.ctx.session.flush()
        await self.activity.record_update(_ENTITY, row.id, before, snapshot(row, _TRACKED))
        return row

    async def unlink(self, container_pk: uuid.UUID) -> None:
        """Deactivate rather than delete — and **never** touch the container at Google.

        Two separate reasons. The conversions recorded against this row outlive the link, and a
        re-link must find the same row rather than mint a second one the unique constraint would
        refuse anyway. And unlinking is a statement about *our* records: an agency that stops
        working for a client does not thereby delete the tracking off their website.
        """
        self.ctx.require("google_tag_manager.settings.manage")
        row = await self.get_container(container_pk)
        if row.active:
            before = snapshot(row, _TRACKED)
            row.active = False
            await self.activity.record_update(_ENTITY, row.id, before, snapshot(row, _TRACKED))

    # --- credentials ------------------------------------------------------------------------- #

    async def _own_connection(self) -> GoogleConnection:
        """The *caller's* own Google grant — what the picker and a fresh link use.

        Distinct from the container's stored connection below: linking is something a person does
        with their own access, while a nightly sync runs on whichever grant the row remembers.
        """
        connection = await google_client.connection_for(
            self.ctx.session, self.ctx.org.id, self.ctx.user.id
        )
        if connection is None or connection.status != ConnectionStatus.ACTIVE.value:
            raise GtmNotConfigured("no active google connection for this user")
        if not has_tag_manager_read_scope(connection.scopes):
            raise GtmNotConfigured("the google connection does not carry the tag manager scopes")
        return connection

    async def connection_for_container(self, row: GtmContainer) -> GoogleConnection:
        if row.connection_id is None:
            raise GtmNotConfigured("this container has no google connection")
        connection = await self.ctx.session.scalar(
            select(GoogleConnection).where(
                GoogleConnection.org_id == self.ctx.org.id,
                GoogleConnection.id == row.connection_id,
            )
        )
        if connection is None:
            raise GtmNotConfigured("this container has no google connection")
        if connection.status != ConnectionStatus.ACTIVE.value:
            raise GtmNotConfigured("the google connection for this container needs reconnecting")
        if not has_tag_manager_read_scope(connection.scopes):
            raise GtmNotConfigured("the google connection does not carry the tag manager scopes")
        return connection

    @asynccontextmanager
    async def open_client(
        self, container_pk: uuid.UUID, *, tool: str = ""
    ) -> AsyncIterator[tuple[GtmClient, GtmContainer]]:
        """The one way anything here reaches Google: a client bound to a linked container, with
        the pooled database connection **released** for the duration.

        Everything the session is needed for — the row, the connection, its tokens — is read
        before the release, in that order, because the first statement after the block re-binds
        the RLS GUC and nothing inside it may query.
        """
        row = await self.get_container(container_pk)
        connection = await self.connection_for_container(row)
        async with (
            gtm_client(self.ctx.session, self.ctx.org, connection, tool=tool) as client,
            self.ctx.release_db(),
        ):
            yield client, row

    # --- the live picker --------------------------------------------------------------------- #

    async def available_containers(self, query: str = "") -> PickerResult:
        """Search the containers the caller's own Google grant can reach — **a search, not a list**.

        The first version listed every account and then every account's containers, which is
        ``1 + n`` requests where *n* belongs to the agency and not to us. Against a real reseller
        grant — 44 Tag Manager accounts — that refused on the 45th call with Tag Manager's
        per-user-per-minute quota, so the control that exists to find a container could not find
        any. Three rules came out of it and none of them is about GTM.

        **The account list is the cheap half and the containers are the expensive half**, so the
        accounts are read always (one call) and containers only for the accounts a search picked.
        **A GTM id short-circuits the whole thing**: ``accounts/containers:lookup`` answers in one
        request and is what somebody pasting an id off a client's website actually wants, so it
        never costs a sweep. And **what was not opened is named** — ``accounts_read`` of
        ``accounts_total``, plus a warning — because a short list that looks complete is the
        failure mode §17 exists to prevent, and the number is what turns "nothing found" into
        "search by account name" rather than into "we are not in that account".

        Live on every request, for the reason the Ads picker is: a picker showing a stale list is
        how somebody links a container that was deleted last month. One account that refuses is
        skipped rather than emptying the result — a user holding five clients' Tag Manager
        accounts and one revoked grant should still see four.
        """
        self.ctx.require("google_tag_manager.settings.manage")
        connection = await self._own_connection()
        linked = {row.container_id for row in await self.list_containers()}
        needle = query.strip()

        warnings: list[str] = []
        options: list[AvailableContainer] = []
        accounts_total = 0
        opened = 0
        async with (
            gtm_client(self.ctx.session, self.ctx.org, connection, tool="picker") as client,
            self.ctx.release_db(),
        ):
            accounts = await client.list("accounts", "account")
            accounts_total = len(accounts)
            if accounts_total > MAX_PICKER_ACCOUNTS:
                warnings.append("gtm.warning.accounts_capped")
                accounts = accounts[:MAX_PICKER_ACCOUNTS]
            by_id = {str(a.get("accountId") or ""): a for a in accounts}

            if _PUBLIC_ID_RE.match(needle):
                # One request, and the exact answer. A id that names nothing this grant can reach
                # is an empty result rather than a refusal: on a *search* box, "no match" is an
                # ordinary outcome and an error envelope would be a wrong sentence about it.
                try:
                    found = await client.get(
                        "accounts/containers:lookup", params={"tagId": needle.upper()}
                    )
                except GtmNotFoundError:
                    found = {}
                except GtmError:
                    warnings.append("gtm.warning.account_unreadable")
                    found = {}
                if found:
                    account = by_id.get(str(found.get("accountId") or ""), {})
                    option = _option(found, account, linked)
                    if option is not None:
                        options.append(option)
                        opened = 1
            else:
                candidates = [a for a in accounts if _matches_account(a, needle)]
                if len(candidates) > MAX_SEARCH_ACCOUNTS:
                    warnings.append("gtm.warning.narrow_search")
                candidates.sort(key=lambda a: str(a.get("name") or "").casefold())
                for account in candidates[:MAX_SEARCH_ACCOUNTS]:
                    account_path = str(account.get("path") or "")
                    if not account_path:
                        continue
                    try:
                        containers = await client.list(f"{account_path}/containers", "container")
                    except GtmQuotaError:
                        # A rate, not a verdict: keep what was read, say the reading stopped, and
                        # let the caller narrow rather than wait out a minute they cannot see.
                        warnings.append("gtm.warning.quota")
                        break
                    except GtmError:
                        # One inaccessible account must not empty the picker (Cloudflare's rule: a
                        # probe that fails is evidence about that probe, not a verdict on all).
                        warnings.append("gtm.warning.account_unreadable")
                        continue
                    opened += 1
                    for container in containers:
                        if len(options) >= MAX_PICKER_CONTAINERS:
                            warnings.append("gtm.warning.containers_capped")
                            break
                        option = _option(container, account, linked)
                        if option is not None:
                            options.append(option)
        options.sort(key=lambda o: (o.account_name.casefold(), o.name.casefold()))
        return PickerResult(
            containers=options,
            warnings=tuple(dict.fromkeys(warnings)),
            accounts_total=accounts_total,
            accounts_read=opened,
        )

    async def _lookup(self, connection: GoogleConnection, tag_id: str) -> dict[str, Any]:
        """``GTM-NPGFR9W9`` → the container, through ``accounts/containers:lookup``."""
        async with (
            gtm_client(self.ctx.session, self.ctx.org, connection, tool="lookup") as client,
            self.ctx.release_db(),
        ):
            try:
                return await client.get("accounts/containers:lookup", params={"tagId": tag_id})
            except GtmNotFoundError as exc:
                raise AppError(
                    "validation",
                    "errors.validation",
                    status_code=422,
                    fields={"public_id": "errors.gtm_container_not_found"},
                ) from exc

    # --- health + observation ---------------------------------------------------------------- #

    async def verify(self, container_pk: uuid.UUID) -> GtmContainer:
        """Ask Google what it says about this container, and record the answer either way.

        Success **clears** ``status`` and ``last_error``, for the reason stated in the module
        docstring. Failure records Google's own sentence and returns the row — a probe that
        raised would leave the caller with an error envelope and the row unchanged, which is the
        one outcome that teaches nobody anything.
        """
        self.ctx.require("google_tag_manager.settings.manage")
        return await self.observe(container_pk)

    async def observe(self, container_pk: uuid.UUID) -> GtmContainer:
        """Refresh everything this row mirrors: the container, the live version, staged changes.

        Called by ``verify`` and by the nightly cron, deliberately as the same function — two
        code paths asking Google the same question is how a screen and a cron come to disagree
        about whether a client's container is healthy.
        """
        row = await self.get_container(container_pk)
        try:
            connection = await self.connection_for_container(row)
            async with (
                gtm_client(self.ctx.session, self.ctx.org, connection, tool="observe") as client,
                self.ctx.release_db(),
            ):
                observed = await self._read_container_state(client, row.path)
        except GtmNotConfigured as exc:
            return self._record_failure(row, str(exc))
        except GtmError as exc:
            return self._record_failure(row, describe_failure(exc))

        self._apply_container_payload(row, observed["container"])
        row.live_version_id = observed["live_version_id"]
        row.live_version_name = observed["live_version_name"]
        row.tag_count = observed["tag_count"]
        row.trigger_count = observed["trigger_count"]
        row.variable_count = observed["variable_count"]
        row.workspace_changes = observed["workspace_changes"]
        row.observed_at = datetime.now(UTC)
        row.status = GtmContainerStatus.ACTIVE.value
        row.last_error = None
        row.last_verified_at = datetime.now(UTC)
        row.last_synced_at = datetime.now(UTC)
        return row

    def _record_failure(self, row: GtmContainer, message: str) -> GtmContainer:
        row.status = GtmContainerStatus.ERROR.value
        row.last_error = message[:500]
        row.last_verified_at = datetime.now(UTC)
        return row

    @staticmethod
    def _apply_container_payload(row: GtmContainer, payload: dict[str, Any]) -> None:
        """What Google said about the container itself. Never anything schakl decided."""
        row.name = str(payload.get("name") or row.name or row.container_id)
        row.public_id = str(payload.get("publicId") or row.public_id)
        row.path = str(payload.get("path") or row.path)
        row.account_id = str(payload.get("accountId") or row.account_id)
        row.container_id = str(payload.get("containerId") or row.container_id)
        row.usage_context = [str(v) for v in (payload.get("usageContext") or [])]
        row.domain_names = [str(v) for v in (payload.get("domainName") or [])]
        row.tagging_server_urls = [str(v) for v in (payload.get("taggingServerUrls") or [])]

    async def _read_container_state(self, client: GtmClient, path: str) -> dict[str, Any]:
        """Container + live version + staged changes, in one place so verify and cron agree."""
        container = await client.get(path)

        live_version_id: str | None = None
        live_version_name: str | None = None
        tag_count = trigger_count = variable_count = 0
        try:
            live = await client.get(f"{path}/versions:live")
        except GtmNotFoundError:
            # A container that has never been published. Ordinary — somebody made it last week —
            # and emphatically not a broken row.
            live = None
        if live:
            live_version_id = str(live.get("containerVersionId") or "") or None
            live_version_name = str(live.get("name") or "") or None
            tag_count = len(live.get("tag") or [])
            trigger_count = len(live.get("trigger") or [])
            variable_count = len(live.get("variable") or [])

        workspace_changes = 0
        try:
            workspaces = await client.list(f"{path}/workspaces", "workspace")
        except GtmError:
            workspaces = []
        for workspace in workspaces[:MAX_OBSERVED_WORKSPACES]:
            workspace_path = str(workspace.get("path") or "")
            if not workspace_path:
                continue
            try:
                status = await client.get(f"{workspace_path}/status")
            except GtmError:
                continue
            workspace_changes += len(status.get("workspaceChange") or [])

        return {
            "container": container,
            "live_version_id": live_version_id,
            "live_version_name": live_version_name,
            "tag_count": tag_count,
            "trigger_count": trigger_count,
            "variable_count": variable_count,
            "workspace_changes": workspace_changes,
        }

    # --- conversions (the records, not the writing) ------------------------------------------- #

    async def list_conversions(self, container_pk: uuid.UUID) -> list[GtmConversion]:
        row = await self.get_container(container_pk)
        stmt = (
            self.ctx.repo(GtmConversion)
            .scoped_select()
            .where(GtmConversion.container_id == row.id)
            .order_by(GtmConversion.name)
        )
        return list((await self.ctx.session.scalars(stmt)).all())

    async def conversion_counts(
        self, container_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, tuple[int, int]]:
        """``{container id: (how many, how many live)}`` in **one** query.

        One grouped read rather than a loop, because the caller is a company panel: an endpoint
        that is one query at two containers and one-per-container at three hundred passes every
        functional test either way, which is exactly the shape docs/PERFORMANCE.md asks to be
        pinned with a count budget rather than noticed later.
        """
        if not container_ids:
            return {}
        repo = self.ctx.repo(GtmConversion)
        stmt = (
            select(
                GtmConversion.container_id,
                func.count().label("total"),
                func.count()
                .filter(GtmConversion.status == GtmConversionStatus.LIVE.value)
                .label("live"),
            )
            .where(
                GtmConversion.org_id == self.ctx.org.id,
                GtmConversion.container_id.in_(container_ids),
            )
            .group_by(GtmConversion.container_id)
        )
        # A hand-built aggregate cannot come from ``scoped_select()``, so the horizon is taken
        # from the one place that owns it (§15, failure mode 3) and ANDed on — even though every
        # id here already came out of a horizon-filtered list, because "the caller narrowed it"
        # is a property of today's caller and not of this query.
        horizon = repo.horizon_condition()
        if horizon is not None:
            stmt = stmt.where(horizon)
        rows = (await self.ctx.session.execute(stmt)).all()
        return {row[0]: (int(row[1]), int(row[2])) for row in rows}
