"""Connecting, verifying and configuring a Timeon organisation. Business-licensed — see LICENSE.

Three rules this file does not bend, all of them learned elsewhere in this codebase.

**Verify never raises.** ``require_context`` rolls the session back on any exception, so raising
from a verify would discard the very row that records what Timeon said — the failure detail
vanishes and the screen shows the account exactly as it was. The result is *returned*, the row
keeps the words on ``last_error``, and the screen renders them.

**The credential is read in exactly one place** (:func:`client_for`). Fernet at rest, never
echoed, never logged, never in an error envelope or the activity trail.

**Three refusals, three keys.** A key Timeon rejects, an edge that blocked us before Timeon saw
the request, and a host that did not answer are three different faults with three different
people who can fix them. Collapsing them into "Timeon is unreachable" sends an agency to check
their internet connection about an expired API key (#381's rule, and #278's before it: a probe
is evidence, and the sentence it produces is half the value).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func

from app.core.activity import ActivityService
from app.core.crypto import decrypt, encrypt
from app.core.tenancy import RequestContext
from app.errors import AppError
from app.integrations.timeon.client import (
    TimeonAuthError,
    TimeonBlockedError,
    TimeonClient,
    TimeonError,
)
from app.integrations.timeon.models import (
    TimeonAccount,
    TimeonAccountStatus,
    TimeonConflict,
    TimeonConflictStatus,
    TimeonLink,
    TimeonSyncRun,
)
from app.integrations.timeon.schemas import (
    TimeonAccountCreate,
    TimeonAccountUpdate,
    TimeonVerifyResult,
)

logger = logging.getLogger("schakl.timeon")

ACCOUNT_ENTITY = "timeon_account"

#: The organisation-level switches a push has to respect. Timeon lets an organisation turn its
#: own optional fields off, and writing one that is off is refused *by Timeon* halfway through a
#: run — so they are read at verify time and shown, rather than discovered.
FEATURE_FIELDS = (
    "fieldProject",
    "fieldRemark",
    "fieldBillable",
    "fieldTimer",
    "fieldDistance",
    "fieldCategory",
    "fieldInternalRemark",
)


def client_for(account: TimeonAccount) -> TimeonClient:
    """A live client for one stored credential.

    Module-level rather than a method for ``mollie``'s reason: decrypting in exactly one place is
    what makes "the key is read once" a checkable claim rather than a hope.
    """
    if not account.api_key_encrypted:
        raise AppError("timeon_not_connected", "errors.timeon.not_connected", status_code=409)
    kwargs: dict[str, Any] = {}
    if account.base_url:
        kwargs["base_url"] = account.base_url
    return TimeonClient(decrypt(account.api_key_encrypted), **kwargs)


def error_key_for(exc: Exception) -> tuple[str, str]:
    """``(i18n key, Timeon's own words)`` for a failed probe. See the module docstring."""
    if isinstance(exc, TimeonAuthError):
        return "errors.timeon.key_refused", str(exc)
    if isinstance(exc, TimeonBlockedError):
        return "errors.timeon.edge_blocked", str(exc)
    if isinstance(exc, TimeonError):
        return "errors.timeon.unreachable", str(exc)
    return "errors.timeon.unreachable", str(exc)


class TimeonAccountService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(TimeonAccount)

    # --- reads --------------------------------------------------------------- #
    async def get_or_404(self, account_id: uuid.UUID) -> TimeonAccount:
        return await self.repo.get_or_404(account_id)

    async def list_accounts(self) -> list[dict[str, Any]]:
        """Every connection, with the pairing counts the screen leads with.

        The counts are two grouped queries for the whole list rather than one pair per account:
        an integration settings screen with three connections must not be six round trips
        (docs/PERFORMANCE.md), and the shape that is one query at three rows and one-per-row at
        thirty is the one that is invisible in the JSON.
        """
        rows = (
            (await self.ctx.session.execute(self.repo.scoped_select().order_by(TimeonAccount.name)))
            .scalars()
            .all()
        )
        if not rows:
            return []
        link_counts: dict[uuid.UUID, dict[str, int]] = {}
        stmt = (
            self.ctx.repo(TimeonLink)
            .scoped_select()
            .with_only_columns(TimeonLink.account_id, TimeonLink.kind, TimeonLink.status,
                               func.count())
            .group_by(TimeonLink.account_id, TimeonLink.kind, TimeonLink.status)
        )
        for account_id, kind, status, total in (await self.ctx.session.execute(stmt)).all():
            link_counts.setdefault(account_id, {})[f"{kind}.{status}"] = int(total)

        conflict_stmt = (
            self.ctx.repo(TimeonConflict)
            .scoped_select()
            .with_only_columns(TimeonConflict.account_id, func.count())
            .where(TimeonConflict.status == TimeonConflictStatus.OPEN.value)
            .group_by(TimeonConflict.account_id)
        )
        open_conflicts = {
            account_id: int(total)
            for account_id, total in (await self.ctx.session.execute(conflict_stmt)).all()
        }
        return [
            self.serialize(
                row,
                counts=link_counts.get(row.id, {}),
                open_conflicts=open_conflicts.get(row.id, 0),
            )
            for row in rows
        ]

    def serialize(
        self,
        account: TimeonAccount,
        *,
        counts: dict[str, int] | None = None,
        open_conflicts: int = 0,
    ) -> dict[str, Any]:
        info = account.organisation_info or {}
        return {
            "id": account.id,
            "name": account.name,
            "connected": bool(account.api_key_encrypted),
            "base_url": account.base_url,
            "organisation_id": account.organisation_id,
            "organisation_name": account.organisation_name,
            "organisation_features": {
                key: bool(info.get(key)) for key in FEATURE_FIELDS if key in info
            },
            "hours_direction": account.hours_direction,
            "projects_direction": account.projects_direction,
            "conflict_policy": account.conflict_policy,
            "window_days": account.window_days,
            "history_floor": account.history_floor,
            "protect_invoiced": account.protect_invoiced,
            "protect_approved": account.protect_approved,
            "push_approvals": account.push_approvals,
            "create_missing_projects": account.create_missing_projects,
            "create_missing_users": account.create_missing_users,
            "auto_sync": account.auto_sync,
            "active": account.active,
            "status": account.status,
            "last_verified_at": account.last_verified_at,
            "last_pull_at": account.last_pull_at,
            "last_push_at": account.last_push_at,
            "last_error": account.last_error,
            "counts": counts or {},
            "open_conflicts": open_conflicts,
        }

    # --- writes -------------------------------------------------------------- #
    async def create_account(self, data: TimeonAccountCreate) -> TimeonAccount:
        """Store a credential. Creating does **not** verify.

        ``/verify`` is the explicit probe, so a typo is reported beside the row on the settings
        screen rather than as a failed save that loses what was typed (``snelstart``'s split).
        The policy starts at ``off`` in both directions on purpose: a connection that began
        syncing the moment a key was pasted would be an irreversible act performed by a form.
        """
        await self._ensure_name_free(data.name)
        account = await self.repo.create(
            name=data.name.strip(),
            api_key_encrypted=encrypt(data.api_key.strip()) if data.api_key else None,
            base_url=(data.base_url or "").strip() or None,
            status=(
                TimeonAccountStatus.ACTIVE.value
                if data.api_key
                else TimeonAccountStatus.PENDING.value
            ),
        )
        await ActivityService(self.ctx).record(
            ACCOUNT_ENTITY, account.id, "created", {"name": account.name}
        )
        return account

    async def update_account(
        self, account_id: uuid.UUID, data: TimeonAccountUpdate
    ) -> TimeonAccount:
        account = await self.get_or_404(account_id)
        values = data.model_dump(exclude_unset=True)
        if "name" in values and values["name"]:
            await self._ensure_name_free(values["name"], exclude=account.id)
            values["name"] = values["name"].strip()
        api_key = values.pop("api_key", None)
        if api_key is not None:
            # Rotating clears the observation with it: what the previous key opened says nothing
            # about what this one does, and leaving the old organisation name on the row is a
            # screen stating a fact that may have stopped being true.
            values["api_key_encrypted"] = encrypt(api_key.strip()) if api_key.strip() else None
            values["status"] = TimeonAccountStatus.PENDING.value
            values["last_verified_at"] = None
            values["last_error"] = None
        if "base_url" in values:
            values["base_url"] = (values["base_url"] or "").strip() or None
        # What actually changed about the *policy* — the half of this row somebody audits later.
        changes = {
            key: {"from": getattr(account, key), "to": values[key]}
            for key in values
            if key not in ("api_key_encrypted",) and getattr(account, key, None) != values[key]
        }
        account = await self.repo.update(account, **values)
        trail: dict[str, Any] = {}
        if api_key is not None:
            trail["credential"] = "rotated"  # never the key itself
        if changes:
            trail["changes"] = {k: {"from": str(v["from"]), "to": str(v["to"])}
                                for k, v in changes.items()}
        if trail:
            await ActivityService(self.ctx).record(ACCOUNT_ENTITY, account.id, "updated", trail)
        return account

    async def delete_account(self, account_id: uuid.UUID) -> None:
        """Forget the connection.

        Links, conflicts and runs cascade — they describe a conversation that no longer exists.
        The *time entries* do not: a pulled entry is schakl's record of work somebody did, and
        deleting a credential is not a statement about whether that work happened. This is the
        same line ``portal`` draws when a licence lapses.
        """
        account = await self.get_or_404(account_id)
        await ActivityService(self.ctx).record(
            ACCOUNT_ENTITY, account.id, "deleted", {"name": account.name}
        )
        await self.repo.delete(account)

    async def _ensure_name_free(self, name: str, *, exclude: uuid.UUID | None = None) -> None:
        stmt = self.repo.scoped_select().where(
            func.lower(TimeonAccount.name) == name.strip().lower()
        )
        if exclude is not None:
            stmt = stmt.where(TimeonAccount.id != exclude)
        if (await self.ctx.session.execute(stmt.limit(1))).scalars().first() is not None:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"name": "errors.timeon.name_taken"},
            )

    # --- verify -------------------------------------------------------------- #
    async def verify(self, account_id: uuid.UUID) -> TimeonVerifyResult:
        """Ask Timeon which organisation this key opens, and how big it is.

        Reads four things rather than one, because the counts are what make the *next* screen
        honest: "157 projects, 7 users, 108 clients" is the shape of the job the sync is about to
        do, and an integration that shows nothing until the first run has already spent the one
        moment an admin was paying attention.

        Each read is its own ``try``. Timeon answers ``/organisation`` for a key with no project
        rights at all, and one ``try`` around four calls would turn a partial permission into
        "the credential is invalid" — the SE Ranking finding (#381), one vendor over.
        """
        account = await self.get_or_404(account_id)
        if not account.api_key_encrypted:
            return TimeonVerifyResult(ok=False, error_key="errors.timeon.not_connected")
        client = client_for(account)
        try:
            org = await client.organisation()
        except Exception as exc:  # noqa: BLE001 - every failure is an answer, never a 500
            key, detail = error_key_for(exc)
            await self.repo.update(
                account,
                status=TimeonAccountStatus.ERROR.value,
                last_error=detail[:500],
                last_verified_at=datetime.now(UTC),
            )
            logger.info("timeon verify failed for %s: %s", account.id, detail)
            return TimeonVerifyResult(ok=False, error_key=key, detail=detail[:500])

        counts: dict[str, int | None] = {"user": None, "project": None, "customer": None}
        for name, call in (
            ("user", client.users),
            ("project", client.projects),
            ("customer", client.customers),
        ):
            try:
                counts[name] = len(await call())
            except Exception as exc:  # noqa: BLE001 - a nicety that rides along fails alone
                logger.info("timeon verify: %s list failed: %s", name, exc)

        await self.repo.update(
            account,
            status=TimeonAccountStatus.ACTIVE.value,
            organisation_id=org.get("organisationID"),
            organisation_name=org.get("name"),
            organisation_info={k: v for k, v in org.items() if k in FEATURE_FIELDS},
            last_verified_at=datetime.now(UTC),
            last_error=None,
        )
        await ActivityService(self.ctx).record(
            ACCOUNT_ENTITY, account.id, "verified", {"organisation": org.get("name")}
        )
        return TimeonVerifyResult(
            ok=True,
            organisation_id=org.get("organisationID"),
            organisation_name=org.get("name"),
            user_count=counts["user"],
            project_count=counts["project"],
            customer_count=counts["customer"],
        )

    # --- runs ---------------------------------------------------------------- #
    async def list_runs(
        self, account_id: uuid.UUID | None, *, limit: int, offset: int
    ) -> tuple[list[TimeonSyncRun], int]:
        repo = self.ctx.repo(TimeonSyncRun)
        stmt = repo.scoped_select().order_by(TimeonSyncRun.created_at.desc())
        count_stmt = repo.scoped_count_select()
        if account_id is not None:
            stmt = stmt.where(TimeonSyncRun.account_id == account_id)
            count_stmt = count_stmt.where(TimeonSyncRun.account_id == account_id)
        rows = (
            (await self.ctx.session.execute(stmt.limit(limit).offset(offset))).scalars().all()
        )
        total = int((await self.ctx.session.execute(count_stmt)).scalar() or 0)
        return list(rows), total
