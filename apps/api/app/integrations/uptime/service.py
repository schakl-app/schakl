"""Business logic for the uptime module — all DB access tenant-scoped (Golden Rule 1).

Two rules shape every method that reaches Uptime Kuma, and both are §3's:

* **The socket call runs in a thread, inside ``ctx.release_db()``.** python-socketio is
  blocking, so calling it on the event loop stalls every other request in the worker; and a
  handshake-plus-login round-trip holds a pooled database connection for a second or more while
  doing no database work, which is the pool drain ``docs/PERFORMANCE.md`` describes. CLAUDE.md
  §3 already states this for ``app/core/ai/``; the pressure here is the same shape.
* **The request path reads the mirror, not Kuma.** Only enrolment, an explicit probe and an
  explicit sync dial out. A list endpoint never does.

And one that shapes what a failure is allowed to do: **a probe is evidence, never the gate.** A
refused credential updates that instance's own status and error, and changes nothing else — the
monitor list keeps rendering what was last observed, because an agency staring at an outage needs
yesterday's mirror far more than it needs an empty screen.
"""

from __future__ import annotations

import asyncio
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import bindparam, func, or_, text

from app.core.activity import ActivityService
from app.core.crypto import decrypt, encrypt
from app.core.tenancy import RequestContext, TenantScopedRepository
from app.errors import AppError
from app.integrations.uptime import errors as kuma_errors
from app.integrations.uptime import matching
from app.integrations.uptime import profiles as prof
from app.integrations.uptime.client import UptimeKumaClient, merge_monitor
from app.integrations.uptime.models import (
    GROUP_TYPE,
    InstanceMode,
    InstanceStatus,
    SyncStatus,
    UptimeInstance,
    UptimeMonitor,
    UptimeMonitorProfile,
)
from app.integrations.uptime.redaction import redact_monitor, secret_drift
from app.integrations.uptime.schemas import (
    UptimeEnrol,
    UptimeInstanceCreate,
    UptimeInstanceUpdate,
    UptimeLinkApplyResult,
    UptimeMonitorCreate,
    UptimeMonitorLink,
    UptimeMonitorUpdate,
    UptimeProbeResult,
    UptimeProfileCreate,
    UptimeProfileUpdate,
    UptimeReconcile,
    UptimeSyncReport,
)

ENTITY_TYPE = "uptime_instance"
MONITOR_ENTITY_TYPE = "uptime_monitor"

#: Definition fields the activity trail tracks (§16). The credential is not among them: the
#: trail records *that* it changed (`token_enrolled`), never a value, and the observed columns
#: are not edits — a sync that wrote a trail line per monitor would bury the one line somebody
#: is looking for under a thousand.
_AUDITED_INSTANCE_FIELDS = ("name", "mode", "base_url", "username", "ssl_verify", "active")

#: What a monitor is attached to on *our* side, and the kinds those columns name. Uptime Kuma
#: has no field for any of it, which is why writing one never pushes (#321).
_ANCHOR_KINDS = ("website", "domain", "hosting")
_ANCHOR_FIELDS = ("website_id", "domain_id", "hosting_id")
_LINK_FIELDS = (*_ANCHOR_FIELDS, "company_id")

#: How an i18n key is chosen for a refusal. Ordered most specific first; the fallback is
#: deliberately generic, because a wrong-but-specific message is worse than an honest vague one.
_ERROR_KEYS: tuple[tuple[type[Exception], str], ...] = (
    (kuma_errors.ReauthRequired, "errors.uptime_reauth_required"),
    (kuma_errors.RateLimited, "errors.uptime_rate_limited"),
    (kuma_errors.TotpRequired, "errors.uptime_totp_required"),
    (kuma_errors.TotpRejected, "errors.uptime_totp_rejected"),
    (kuma_errors.CredentialsRejected, "errors.uptime_credentials_rejected"),
    (kuma_errors.VersionUnsupported, "errors.uptime_version_unsupported"),
    (kuma_errors.NotUptimeKuma, "errors.uptime_not_kuma"),
    (kuma_errors.GatewayRefused, "errors.uptime_gateway_refused"),
    (kuma_errors.Unreachable, "errors.uptime_unreachable"),
)


def error_key(exc: Exception) -> str:
    for kind, key in _ERROR_KEYS:
        if isinstance(exc, kind):
            return key
    return "errors.uptime_failed"


class UptimeService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.instances = TenantScopedRepository(
            ctx.session, ctx.org.id, UptimeInstance, company_scope=ctx.company_scope
        )
        self.monitors = TenantScopedRepository(
            ctx.session, ctx.org.id, UptimeMonitor, company_scope=ctx.company_scope
        )
        self.profiles = TenantScopedRepository(
            ctx.session, ctx.org.id, UptimeMonitorProfile, company_scope=ctx.company_scope
        )
        self.activity = ActivityService(ctx)

    # ------------------------------------------------------------------ instances

    async def list_instances(self) -> list[UptimeInstance]:
        stmt = self.instances.scoped_select().order_by(UptimeInstance.name)
        return list((await self.ctx.session.execute(stmt)).scalars().all())

    async def get_instance(self, instance_id: uuid.UUID) -> UptimeInstance:
        return await self.instances.get_or_404(instance_id)

    async def monitor_counts(
        self, instance_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, tuple[int, int]]:
        """``(monitors, groups)`` per instance in **one** grouped query, never one per row.

        The shape `docs/PERFORMANCE.md` exists to prevent: correct and cheap at three instances,
        correct and linear at three hundred, and identical in the JSON either way. The group
        count rides the same statement as a filtered aggregate rather than arriving as a second
        query — it is the same rows, asked a narrower question.

        Groups are counted **and** included in the total, because a group is a monitor here and
        subtracting it would make the number disagree with the list the same screen links to.
        """
        if not instance_ids:
            return {}
        groups = func.count(UptimeMonitor.id).filter(UptimeMonitor.monitor_type == GROUP_TYPE)
        stmt = (
            self.monitors.scoped_select()
            .with_only_columns(UptimeMonitor.instance_id, func.count(UptimeMonitor.id), groups)
            .where(UptimeMonitor.instance_id.in_(instance_ids))
            .group_by(UptimeMonitor.instance_id)
        )
        return {
            row[0]: (int(row[1]), int(row[2] or 0))
            for row in (await self.ctx.session.execute(stmt)).all()
        }

    async def create_instance(self, payload: UptimeInstanceCreate) -> UptimeInstance:
        if payload.mode == InstanceMode.MANAGED and not payload.base_url:
            raise AppError(
                "validation_error", "errors.uptime_base_url_required", fields={"base_url": ""}
            )
        instance = await self.instances.create(
            name=payload.name,
            mode=payload.mode.value,
            base_url=payload.base_url,
            ssl_verify=payload.ssl_verify,
            active=payload.active,
            # Both minted here and never rotated by an edit: the salt would invalidate every
            # stored fingerprint, and the webhook secret is printed into a URL somebody has
            # already configured at the far end.
            secret_salt=secrets.token_urlsafe(32),
            webhook_secret=secrets.token_urlsafe(32),
            status=InstanceStatus.PENDING.value,
        )
        await self.activity.record(ENTITY_TYPE, instance.id, "created")
        return await self._settled(instance)

    async def update_instance(
        self, instance_id: uuid.UUID, payload: UptimeInstanceUpdate
    ) -> UptimeInstance:
        instance = await self.instances.get_or_404(instance_id)
        before = {f: getattr(instance, f) for f in _AUDITED_INSTANCE_FIELDS}

        values = payload.model_dump(exclude_unset=True)
        headers = values.pop("connect_headers", None)
        if "mode" in values and values["mode"] is not None:
            values["mode"] = InstanceMode(values["mode"]).value
        for field, value in values.items():
            if value is not None:
                setattr(instance, field, value)

        if headers is not None:
            # Explicit `{}` clears; absent never reaches here (`exclude_unset`). §18's rule.
            instance.connect_headers_encrypted = (
                encrypt(_dump_headers(headers)) if headers else None
            )
            await self.activity.record(ENTITY_TYPE, instance.id, "headers_changed")

        if instance.mode == InstanceMode.MANAGED.value and not instance.base_url:
            raise AppError("validation_error", "errors.uptime_base_url_required")

        changes = {
            f: {"from": before[f], "to": getattr(instance, f)}
            for f in _AUDITED_INSTANCE_FIELDS
            if before[f] != getattr(instance, f)
        }
        if changes:
            await self.activity.record(
                ENTITY_TYPE, instance.id, "updated", payload={"changes": changes}
            )
        return await self._settled(instance)

    async def delete_instance(self, instance_id: uuid.UUID) -> None:
        """Delete locally and touch **nothing** at Uptime Kuma.

        Deleting a client's live monitoring as a side effect of tidying a credential list is
        unrecoverable, and nothing about "remove this connection" implies it.
        """
        instance = await self.instances.get_or_404(instance_id)
        await self.activity.record(ENTITY_TYPE, instance.id, "deleted")
        await self.ctx.session.delete(instance)

    # ------------------------------------------------------------- talking to kuma

    def _connect_kwargs(self, instance: UptimeInstance) -> dict[str, Any]:
        if instance.mode != InstanceMode.MANAGED.value:
            raise AppError("validation_error", "errors.uptime_linked_instance", status_code=409)
        if not instance.base_url:
            raise AppError("validation_error", "errors.uptime_base_url_required")
        headers = (
            _load_headers(decrypt(instance.connect_headers_encrypted))
            if instance.connect_headers_encrypted
            else {}
        )
        return {
            "base_url": instance.base_url,
            "headers": headers,
            "ssl_verify": instance.ssl_verify,
        }

    async def _in_kuma(self, instance: UptimeInstance, work) -> Any:
        """Run ``work(client)`` against a connected instance, off the loop and off the pool.

        ``asyncio.to_thread`` because the client blocks; ``release_db()`` because the round trip
        would otherwise pin a pooled connection through a network wait. The connection is closed
        in a ``finally`` inside the thread — a socket left open is what the reference client's
        own docstring warns about.
        """
        kwargs = self._connect_kwargs(instance)

        def _run() -> Any:
            client = UptimeKumaClient(**kwargs)
            client.connect()
            try:
                return work(client)
            finally:
                client.close()

        async with self.ctx.release_db():
            return await asyncio.to_thread(_run)

    async def _settled(self, row: Any) -> Any:
        """Flush, then re-read — the last statement of every write path that returns a row.

        ``TimestampMixin.updated_at`` carries ``onupdate=func.now()``, a **SQL** expression, so
        every flush that updates a row expires that attribute to re-read the server's value.
        Serialising the row afterwards lazy-loads from inside Pydantic — synchronously, in a
        context with no greenlet — and asyncpg answers ``MissingGreenlet``.

        Ordinary write paths never notice, because their flush is the last thing that touches
        the row before the response. This module's are not: an external call sits in the middle
        of several of them, and ``release_db()`` commits on entry, so a row crosses that seam
        twice. It applies to the plain instance edit too, which has no external call at all —
        the expiry is caused by the flush, not by the socket.
        """
        await self.ctx.session.flush()
        await self.ctx.session.refresh(row)
        return row

    async def _reload(self, *rows: Any) -> None:
        """Re-read rows that were loaded before an external call.

        ``release_db()`` **commits** on entry, and a commit expires every loaded ORM object. An
        expired attribute then lazy-loads on first read — synchronously, from inside Pydantic's
        serialisation — and asyncpg answers ``MissingGreenlet``. Assigning to an attribute hides
        it (that un-expires without loading), which is why the instance paths appeared to work
        while anything reading ``updated_at`` did not.

        So: refresh explicitly, in async code, before anything reads a row across that seam.
        """
        for row in rows:
            if row is not None:
                await self.ctx.session.refresh(row)

    def _mark(
        self,
        instance: UptimeInstance,
        *,
        status: InstanceStatus,
        error: str | None,
        version: str | None = None,
    ) -> None:
        """Record the outcome of a call — and **clear** the error when it worked.

        A health flag that only ever turns on is a bug with a long tail: `cloudflare` shipped one
        and rows nothing was wrong with kept a red line through every sync that succeeded. Every
        setter of `status` goes through here, so there is exactly one place that says what clears
        it.
        """
        instance.status = status.value
        instance.last_error = error
        instance.last_checked_at = datetime.now(UTC)
        if version:
            instance.server_version = version

    async def enrol(self, instance_id: uuid.UUID, payload: UptimeEnrol) -> UptimeProbeResult:
        """Authenticate once with a password and keep the token instead of it.

        The password and the TOTP code are used inside this call and **never written**: what is
        stored is Kuma's JWT, which carries no expiry, holds neither factor, and is revoked by a
        password change, a deactivated user or a `jwtSecret` rotation. That is the only reduction
        in blast radius available, because Kuma has no service accounts — whatever is enrolled
        here is that instance's administrator.
        """
        instance = await self.instances.get_or_404(instance_id)
        if payload.connect_headers is not None:
            instance.connect_headers_encrypted = (
                encrypt(_dump_headers(payload.connect_headers)) if payload.connect_headers else None
            )

        def _work(client: UptimeKumaClient) -> tuple[str, str]:
            token = client.enrol(payload.username, payload.password, totp=payload.totp)
            client.require_supported_version()
            return token, client.server_version or ""

        try:
            token, version = await self._in_kuma(instance, _work)
        except kuma_errors.UptimeKumaError as exc:
            self._mark(instance, status=InstanceStatus.ERROR, error=str(exc)[:500])
            await self.ctx.session.flush()
            return UptimeProbeResult(
                ok=False, status=instance.status, error=error_key(exc), detail=str(exc)[:200]
            )

        instance.username = payload.username
        instance.token_encrypted = encrypt(token)
        self._mark(instance, status=InstanceStatus.ACTIVE, error=None, version=version)
        await self.activity.record(ENTITY_TYPE, instance.id, "token_enrolled")
        await self.ctx.session.flush()
        return UptimeProbeResult(ok=True, status=instance.status, server_version=version)

    async def probe(self, instance_id: uuid.UUID) -> UptimeProbeResult:
        """Check the stored token still works, and record what we learned.

        Never raises for a refusal: the answer *is* the report, and an exception here would roll
        back the very status update that makes the failure visible.
        """
        instance = await self.instances.get_or_404(instance_id)
        if not instance.token_encrypted:
            return UptimeProbeResult(
                ok=False, status=instance.status, error="errors.uptime_not_enrolled"
            )
        token = decrypt(instance.token_encrypted)

        def _work(client: UptimeKumaClient) -> str:
            client.authenticate(token)
            return client.require_supported_version()

        try:
            version = await self._in_kuma(instance, _work)
        except kuma_errors.ReauthRequired as exc:
            self._mark(instance, status=InstanceStatus.NEEDS_REAUTH, error=str(exc)[:500])
            await self.ctx.session.flush()
            return UptimeProbeResult(
                ok=False, status=instance.status, error="errors.uptime_reauth_required"
            )
        except kuma_errors.UptimeKumaError as exc:
            self._mark(instance, status=InstanceStatus.ERROR, error=str(exc)[:500])
            await self.ctx.session.flush()
            return UptimeProbeResult(
                ok=False, status=instance.status, error=error_key(exc), detail=str(exc)[:200]
            )

        self._mark(instance, status=InstanceStatus.ACTIVE, error=None, version=version)
        await self.ctx.session.flush()
        return UptimeProbeResult(ok=True, status=instance.status, server_version=version)

    # ------------------------------------------------------------------- the sync

    async def sync(self, instance_id: uuid.UUID) -> UptimeSyncReport:
        """Read every monitor and refresh the mirror. **Writes nothing to Kuma.**

        Gate 1 is read-only on purpose: adopting an existing instance is the first thing an
        agency does, and it must be impossible for that to modify a client's live monitoring
        while somebody is still deciding whether the links are right.
        """
        instance = await self.instances.get_or_404(instance_id)
        if not instance.token_encrypted:
            return UptimeSyncReport(
                instance_id=instance.id, ok=False, error="errors.uptime_not_enrolled"
            )
        token = decrypt(instance.token_encrypted)

        def _work(client: UptimeKumaClient) -> tuple[dict[int, dict], str]:
            client.authenticate(token)
            version = client.require_supported_version()
            return client.list_monitors(), version

        try:
            remote, version = await self._in_kuma(instance, _work)
        except kuma_errors.ReauthRequired as exc:
            self._mark(instance, status=InstanceStatus.NEEDS_REAUTH, error=str(exc)[:500])
            await self.ctx.session.flush()
            return UptimeSyncReport(
                instance_id=instance.id, ok=False, error="errors.uptime_reauth_required"
            )
        except kuma_errors.UptimeKumaError as exc:
            self._mark(instance, status=InstanceStatus.ERROR, error=str(exc)[:500])
            await self.ctx.session.flush()
            return UptimeSyncReport(instance_id=instance.id, ok=False, error=error_key(exc))

        report = await self._apply_sync(instance, remote)
        self._mark(instance, status=InstanceStatus.ACTIVE, error=None, version=version)
        instance.last_synced_at = datetime.now(UTC)
        report.server_version = version
        await self.ctx.session.flush()
        return report

    async def _apply_sync(
        self, instance: UptimeInstance, remote: dict[int, dict[str, Any]]
    ) -> UptimeSyncReport:
        """Fold Kuma's answer into the mirror: create, update, and mark what has vanished."""
        stmt = self.monitors.scoped_select().where(UptimeMonitor.instance_id == instance.id)
        existing = {
            row.kuma_monitor_id: row
            for row in (await self.ctx.session.execute(stmt)).scalars().all()
            if row.kuma_monitor_id is not None
        }
        report = UptimeSyncReport(
            instance_id=instance.id,
            ok=True,
            seen=len(remote),
            # Counted from Kuma's answer rather than from our rows, so the number describes what
            # was *read* even on a sync that then failed to write something.
            groups=sum(1 for m in remote.values() if m.get("type") == GROUP_TYPE),
        )
        now = datetime.now(UTC)

        for kuma_id, payload in remote.items():
            snapshot = redact_monitor(payload, salt=instance.secret_salt)
            row = existing.pop(kuma_id, None)
            if row is None:
                row = await self.monitors.create(
                    instance_id=instance.id,
                    name=str(payload.get("name") or f"monitor {kuma_id}")[:255],
                    monitor_type=str(payload.get("type") or "http")[:40],
                    target=_target_of(payload),
                    port=_int_or_none(payload.get("port")),
                    interval_seconds=_int_or_none(payload.get("interval")),
                    retries=_int_or_none(payload.get("maxretries")),
                    active=bool(payload.get("active", True)),
                    kuma_monitor_id=kuma_id,
                    remote_snapshot=snapshot,
                    last_observed_at=now,
                    sync_status=SyncStatus.ACTIVE.value,
                )
                report.created += 1
            else:
                # Gate 1 has no locally-decided state to disagree with yet, so an observation is
                # simply the truth. Gate 2 is where `drift` becomes a comparison rather than an
                # overwrite — and `secret_drift` is already here so that the credential half of
                # that comparison is testable before the rest of it exists.
                credential_moved = bool(secret_drift(row.remote_snapshot or {}, snapshot))
                # Gate 2: an observation is only *truth* for a monitor we adopted. One schakl
                # created has intent of its own, so a difference is drift — reported, never
                # quietly absorbed, or "somebody changed this in Kuma" becomes unsayable again.
                drifted = prof.compute_drift(row, payload)
                if row.adopted:
                    row.name = str(payload.get("name") or row.name)[:255]
                    row.monitor_type = str(payload.get("type") or row.monitor_type)[:40]
                    row.target = _target_of(payload)
                    row.port = _int_or_none(payload.get("port"))
                    row.interval_seconds = _int_or_none(payload.get("interval"))
                    row.retries = _int_or_none(payload.get("maxretries"))
                # `active` follows Kuma either way: pausing is not a configuration conflict.
                row.active = bool(payload.get("active", True))
                row.remote_snapshot = snapshot
                row.last_observed_at = now
                row.drift_fields = list(drifted)
                row.sync_status = SyncStatus.DRIFT.value if drifted else SyncStatus.ACTIVE.value
                row.last_error = "uptime.drift.credential" if credential_moved else None
                if drifted:
                    report.drifted += 1
                report.updated += 1

        # Anything left in `existing` is a monitor Kuma no longer has. Marked, never deleted:
        # "it is gone from Kuma" and "we should forget it" are different decisions, and only one
        # of them is ours to make.
        for row in existing.values():
            row.sync_status = SyncStatus.MISSING.value
            row.last_observed_at = now
            report.missing += 1

        await self.ctx.session.flush()
        await self._link_parents(instance, report)
        await self._match_links(instance, report)
        return report

    async def _link_parents(self, instance: UptimeInstance, report: UptimeSyncReport) -> None:
        """Resolve Kuma's integer ``parent`` into our own ``parent_id`` — and report a move.

        A second pass because a child can arrive before its group: Kuma's ids are not ordered by
        hierarchy, and resolving inline would drop every edge that pointed forwards.

        Three cases, and only the first was here before (#321). A monitor **we have never
        pushed** is left alone: its snapshot is empty, so reading a parent out of it would set
        every pending monitor's group to `None` and quietly undo the group somebody picked on
        the create form. An **adopted** monitor takes Kuma's answer, because an observation is
        simply the truth for a row with no intent of its own. And a monitor **we created** that
        somebody moved in Kuma is *drift* — reported, never absorbed, exactly as its interval
        would be. That last case is the whole reason `adopted` exists, and the group was the one
        field it was not being applied to.
        """
        stmt = self.monitors.scoped_select().where(UptimeMonitor.instance_id == instance.id)
        rows = list((await self.ctx.session.execute(stmt)).scalars().all())
        by_kuma = {r.kuma_monitor_id: r for r in rows if r.kuma_monitor_id is not None}
        for row in rows:
            if row.kuma_monitor_id is None:
                continue
            parent = (row.remote_snapshot or {}).get("parent")
            target = by_kuma.get(parent) if isinstance(parent, int) else None
            observed_id = target.id if target is not None else None
            if row.adopted:
                row.parent_id = observed_id
                continue
            fields = [f for f in (row.drift_fields or []) if f != "parent_id"]
            if row.parent_id != observed_id:
                # Counted only when nothing else had already flagged this row, or one monitor
                # whose interval *and* group moved would be two in a report of one number.
                if not fields:
                    report.drifted += 1
                fields.append("parent_id")
                row.sync_status = SyncStatus.DRIFT.value
            elif not fields and row.sync_status == SyncStatus.DRIFT.value:
                row.sync_status = SyncStatus.ACTIVE.value
            row.drift_fields = fields
        await self.ctx.session.flush()

    async def _match_links(self, instance: UptimeInstance, report: UptimeSyncReport) -> None:
        """Propose the website or domain each unlinked monitor watches (:mod:`.matching`, #321).

        **Proposals only.** Nothing here writes a link, for docs/UPTIME.md §9's reason: adoption
        never decides on a guess, and its output is our mirror plus a set of links a human
        confirmed. What it does write is the *observation* — the candidates and the moment they
        were looked for — so the reconciliation screen survives a page reload and can tell "we
        looked and found nothing" apart from "nobody has ever looked".

        Two statements for the whole instance, whatever its size: one read of the monitors and
        one of the domains their hostnames could name (docs/PERFORMANCE.md). A per-monitor
        lookup would be correct at three monitors and linear at three hundred, and identical in
        the JSON either way.

        Monitors that are **already linked** are skipped and keep whatever they had: a link a
        person made is not a question this pass is entitled to re-ask. So are groups — a group
        watches nothing, so it has no hostname to match and listing it as "unmatched" would fill
        the screen with rows nobody can act on.
        """
        stmt = self.monitors.scoped_select().where(
            UptimeMonitor.instance_id == instance.id,
            UptimeMonitor.monitor_type != GROUP_TYPE,
            UptimeMonitor.website_id.is_(None),
            UptimeMonitor.domain_id.is_(None),
            UptimeMonitor.hosting_id.is_(None),
        )
        rows = list((await self.ctx.session.execute(stmt)).scalars().all())
        if not rows:
            return

        hosts = {h for h in (matching.host_of(r.target) for r in rows) if h}
        names = matching.lookup_hosts(hosts)
        websites: dict[str, list[matching.LinkCandidate]] = {}
        domains: dict[str, list[matching.LinkCandidate]] = {}
        if names:
            found = (
                await self.ctx.session.execute(
                    matching.index_query(self.ctx.org.id, self.ctx.company_scope, names)
                )
            ).all()
            websites, domains = matching.build_index(found)

        now = datetime.now(UTC)
        for row in rows:
            candidates = matching.candidates_for(matching.host_of(row.target), websites, domains)
            row.link_candidates = [c.as_json() for c in candidates]
            row.link_checked_at = now
            if len(candidates) == 1:
                report.matched += 1
            elif candidates:
                report.ambiguous += 1
            else:
                report.unmatched += 1
        await self.ctx.session.flush()

    # ------------------------------------------------------------------- monitors

    async def list_monitors(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        instance_id: uuid.UUID | None = None,
        company_id: uuid.UUID | None = None,
        website_id: uuid.UUID | None = None,
        domain_id: uuid.UUID | None = None,
        hosting_id: uuid.UUID | None = None,
        sync_status: str | None = None,
        monitor_type: str | None = None,
        link_status: str | None = None,
        count: bool = True,
    ) -> tuple[list[UptimeMonitor], int]:
        # Built once and spread into both statements. Two hand-written copies of the same
        # filters is how a `total` starts disagreeing with the list under it — the shape #285
        # names as failure mode 2, reached here by drift rather than by omission.
        conditions = _monitor_filters(
            instance_id=instance_id,
            company_id=company_id,
            website_id=website_id,
            domain_id=domain_id,
            hosting_id=hosting_id,
            sync_status=sync_status,
            monitor_type=monitor_type,
            link_status=link_status,
        )
        stmt = self.monitors.scoped_select().where(*conditions)

        total: int | None = None
        if count:
            # `scoped_count_select`, never a hand-built count: a total built any other way skips
            # the company horizon and shows "2" above a list of one (#285, failure mode 2).
            csel = self.monitors.scoped_count_select().where(*conditions)
            total = int((await self.ctx.session.execute(csel)).scalar() or 0)

        stmt = stmt.order_by(UptimeMonitor.name).limit(limit).offset(offset)
        items = list((await self.ctx.session.execute(stmt)).scalars().all())
        # `count=false` reports the page length, the shape every other index uses — never
        # `None`. `Page.total` is a plain `int`, so handing it one 500s the request, and the
        # only caller that passes `count=false` is the website panel: the panel this module
        # exists to draw answered 500 on every website page that had monitoring.
        return items, total if total is not None else len(items)

    async def group_names(self, monitors: list[UptimeMonitor]) -> dict[uuid.UUID, str]:
        """``parent_id -> group name`` for one page of monitors, in **one** query or none.

        A group is a monitor in the same table, so this is a second read of it rather than a
        join: the page has already been fetched and its parents are almost always a handful of
        rows shared by every monitor on it. Resolved here rather than denormalised onto the
        child, because the group's name is Kuma's to change and a copy would go stale silently.

        Goes through the repository, so the company horizon applies to the parent exactly as it
        does to the child (#285): a group is org-wide furniture, but asking for it by id through
        a raw select is how failure mode (4) gets built by accident.
        """
        parent_ids = {m.parent_id for m in monitors if m.parent_id is not None}
        if not parent_ids:
            return {}
        stmt = self.monitors.scoped_select().where(UptimeMonitor.id.in_(parent_ids))
        stmt = stmt.with_only_columns(UptimeMonitor.id, UptimeMonitor.name)
        return {row[0]: row[1] for row in (await self.ctx.session.execute(stmt)).all()}

    async def child_counts(self, monitors: list[UptimeMonitor]) -> dict[uuid.UUID, int]:
        """How many monitors sit in each group on this page — **one** grouped query.

        What makes the delete guard predictable: a group that refuses to be deleted because it
        still has children should have said "3 monitors" on screen first, rather than answering
        an error to a button that looked available (#321, docs/UX.md).
        """
        group_ids = [m.id for m in monitors if m.monitor_type == GROUP_TYPE]
        if not group_ids:
            return {}
        stmt = (
            self.monitors.scoped_select()
            .with_only_columns(UptimeMonitor.parent_id, func.count(UptimeMonitor.id))
            .where(UptimeMonitor.parent_id.in_(group_ids))
            .group_by(UptimeMonitor.parent_id)
        )
        return {row[0]: row[1] for row in (await self.ctx.session.execute(stmt)).all()}

    async def company_names(self, monitors: list[UptimeMonitor]) -> dict[uuid.UUID, str]:
        """``company_id -> name`` for one page of monitors, in **one** query or none.

        `companies` is another module's table, read as a bare name the way `matching` already
        reads `domains` (§6) — a lookup is not a data path into another module. No horizon clause
        is needed and none would be honest: the ids come off monitors this caller has *already*
        been handed, so naming their client reveals nothing the row did not.

        Filled only under ``meta=true``. It was declared on `UptimeMonitorRead` from the start,
        documented as resolved when asked for, and resolved by nobody — so every list that tried
        to say whose a monitor was printed nothing, and the one screen that needed it kept its
        own map of instance names client-side to work around the same gap.
        """
        ids = {m.company_id for m in monitors if m.company_id is not None}
        if not ids:
            return {}
        stmt = text("SELECT id, name FROM companies WHERE id IN :ids").bindparams(
            bindparam("ids", expanding=True)
        )
        rows = (await self.ctx.session.execute(stmt, {"ids": list(ids)})).all()
        return {row[0]: row[1] for row in rows}

    async def instance_names(self, monitors: list[UptimeMonitor]) -> dict[uuid.UUID, str]:
        """``instance_id -> name`` for one page of monitors, in **one** query or none.

        Through the instance repository, so an instance outside the caller's reach is simply
        absent rather than named.
        """
        ids = {m.instance_id for m in monitors}
        if not ids:
            return {}
        stmt = (
            self.instances.scoped_select()
            .with_only_columns(UptimeInstance.id, UptimeInstance.name)
            .where(UptimeInstance.id.in_(ids))
        )
        return {row[0]: row[1] for row in (await self.ctx.session.execute(stmt)).all()}

    async def get_monitor(self, monitor_id: uuid.UUID) -> UptimeMonitor:
        return await self.monitors.get_or_404(monitor_id)

    async def company_summary(self, company_id: uuid.UUID) -> dict[str, Any]:
        """The company panel's numbers, as **one** grouped query.

        A panel that folded each monitor's status in Python would be the per-row read
        `docs/PERFORMANCE.md` bans, and it is invisible in the JSON either way.
        """
        stmt = (
            self.monitors.scoped_select()
            .with_only_columns(UptimeMonitor.sync_status, func.count(UptimeMonitor.id))
            .where(UptimeMonitor.company_id == company_id)
            .group_by(UptimeMonitor.sync_status)
        )
        rows = (await self.ctx.session.execute(stmt)).all()
        by_status = {row[0]: row[1] for row in rows}
        return {"total": sum(by_status.values()), "by_status": by_status}


def _plain(changes: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """A change map JSONB can hold: a ``UUID`` is not JSON, and an activity row is JSONB.

    Written out rather than left to the driver, because the failure is a 500 at commit time on
    a *write that already succeeded* — the monitor moves and the trail entry takes the request
    down with it.
    """
    return {
        field: {side: (str(value) if isinstance(value, uuid.UUID) else value)
                for side, value in change.items()}
        for field, change in changes.items()
    }


def _sole_candidate(monitor: UptimeMonitor) -> tuple[str, uuid.UUID] | None:
    """The one anchor this monitor proposes, or ``None`` if it proposes none or several.

    Defensive about its own stored shape: ``link_candidates`` is JSONB written by an earlier
    release of this code, so a row from before a change of shape must degrade to "no proposal"
    rather than raise inside a bulk apply somebody is watching a spinner for.
    """
    candidates = monitor.link_candidates or []
    if len(candidates) != 1 or not isinstance(candidates[0], dict):
        return None
    kind = candidates[0].get("entity_type")
    raw = candidates[0].get("entity_id")
    if kind not in matching.LINKABLE or not raw:
        return None
    try:
        return kind, uuid.UUID(str(raw))
    except ValueError:
        return None


def _linked_condition():  # noqa: ANN202 — a SQLAlchemy clause, typed only by SQLAlchemy
    """"Attached to something" as one clause, so every reader means the same by it."""
    return or_(
        UptimeMonitor.website_id.is_not(None),
        UptimeMonitor.domain_id.is_not(None),
        UptimeMonitor.hosting_id.is_not(None),
    )


def _monitor_filters(
    *,
    instance_id: uuid.UUID | None = None,
    company_id: uuid.UUID | None = None,
    website_id: uuid.UUID | None = None,
    domain_id: uuid.UUID | None = None,
    hosting_id: uuid.UUID | None = None,
    sync_status: str | None = None,
    monitor_type: str | None = None,
    link_status: str | None = None,
) -> list[Any]:
    """The list filters, once, for the page and its count alike.

    ``link_status`` is expressed in SQL rather than folded in Python for the reason every other
    filter is: a screen that asks for the unlinked monitors of a thousand-monitor instance must
    get a page, not a prefix of one (CLAUDE.md §9). ``proposed`` is the one the reconciliation
    screen asks for — everything with at least one candidate — because "there is exactly one
    obvious answer" and "there are two and you must choose" belong in the same list, told apart
    by the row rather than by a second request.

    **Every anchor is filterable, not just ``website_id``.** A link you can write is a link
    something has to be able to read back: the domain and hosting columns had a write path, a
    matcher and a horizon, and no way to ask "what watches this one" — so a monitor attached to a
    client's domain was stored correctly and appeared on no screen in the product.
    """
    conditions: list[Any] = []
    if instance_id is not None:
        conditions.append(UptimeMonitor.instance_id == instance_id)
    if company_id is not None:
        conditions.append(UptimeMonitor.company_id == company_id)
    if website_id is not None:
        conditions.append(UptimeMonitor.website_id == website_id)
    if domain_id is not None:
        conditions.append(UptimeMonitor.domain_id == domain_id)
    if hosting_id is not None:
        conditions.append(UptimeMonitor.hosting_id == hosting_id)
    if sync_status is not None:
        conditions.append(UptimeMonitor.sync_status == sync_status)
    if monitor_type is not None:
        conditions.append(UptimeMonitor.monitor_type == monitor_type)
    if link_status is not None:
        found = func.jsonb_array_length(UptimeMonitor.link_candidates)
        if link_status == matching.LINKED:
            conditions.append(_linked_condition())
        else:
            conditions.append(~_linked_condition())
            # `unlinked` is every one of the three unlinked states at once, and it exists because
            # the picker's question is not "what did the matcher find" but "what may I still
            # attach". Asking for `unmatched` there would have offered a monitor whose proposal
            # nobody confirmed and hidden the ones with no proposal at all — which is exactly the
            # set a person opens a picker to reach.
            if link_status == matching.MATCHED:
                conditions.append(found == 1)
            elif link_status == matching.AMBIGUOUS:
                conditions.append(found > 1)
            elif link_status == matching.UNMATCHED:
                conditions.append(found == 0)
            elif link_status == "proposed":
                conditions.append(found > 0)
    return conditions


def _target_of(payload: dict[str, Any]) -> str | None:
    """The one field a reader means by "what is this watching", per monitor type.

    A **group watches nothing**, and Uptime Kuma still stores it a ``url`` of ``"https://"`` —
    its form's placeholder, saved. Copying that through would put a bare scheme in the target
    column of every group, which reads on screen as a monitor pointed at a broken address rather
    than as the folder it is.
    """
    if payload.get("type") == GROUP_TYPE:
        return None
    for key in ("url", "hostname", "docker_container", "databaseConnectionString"):
        value = payload.get(key)
        if value:
            # A connection string is a credential; the mirror keeps the shape, never the secret.
            return "•••" if key == "databaseConnectionString" else str(value)[:1000]
    return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _dump_headers(headers: dict[str, str]) -> str:
    import json

    return json.dumps(headers, separators=(",", ":"))


def _load_headers(raw: str) -> dict[str, str]:
    import json

    try:
        loaded = json.loads(raw)
    except ValueError:
        return {}
    return {str(k): str(v) for k, v in loaded.items()} if isinstance(loaded, dict) else {}


async def visible_header_names(instance: UptimeInstance) -> list[str]:
    """Header **names** only — seeing `CF-Access-Client-Id` listed is how an admin confirms the
    tunnel is wired, and the value is a credential that has no read shape."""
    if not instance.connect_headers_encrypted:
        return []
    return sorted(_load_headers(decrypt(instance.connect_headers_encrypted)))


# ---------------------------------------------------------------------- gate 2


class UptimeWriteService(UptimeService):
    """The write half: profiles, monitors pushed to Uptime Kuma, drift and reconcile.

    Split from the read service only for readability — it is the same class hierarchy and the
    same repositories, so a write path can never reach rows a read path could not.
    """

    # --------------------------------------------------------------- profiles

    async def list_profiles(self) -> list[UptimeMonitorProfile]:
        stmt = self.profiles.scoped_select().order_by(
            UptimeMonitorProfile.position, UptimeMonitorProfile.name
        )
        return list((await self.ctx.session.execute(stmt)).scalars().all())

    async def create_profile(self, payload: UptimeProfileCreate) -> UptimeMonitorProfile:
        profile = await self.profiles.create(
            name=payload.name,
            monitor_type=payload.monitor_type,
            defaults=prof.profile_defaults_input(payload.defaults),
            notification_ids=list(payload.notification_ids or []),
            is_default=payload.is_default,
            active=payload.active,
            position=payload.position,
        )
        if payload.is_default:
            await self._demote_other_defaults(profile)
        await self.activity.record("uptime_monitor_profile", profile.id, "created")
        return profile

    async def update_profile(
        self, profile_id: uuid.UUID, payload: UptimeProfileUpdate
    ) -> UptimeMonitorProfile:
        profile = await self.profiles.get_or_404(profile_id)
        values = payload.model_dump(exclude_unset=True)
        if "defaults" in values and values["defaults"] is not None:
            values["defaults"] = prof.profile_defaults_input(values["defaults"])
        for field, value in values.items():
            if value is not None:
                setattr(profile, field, value)
        if values.get("is_default"):
            await self._demote_other_defaults(profile)
        await self.activity.record("uptime_monitor_profile", profile.id, "updated")
        await self.ctx.session.flush()
        return profile

    async def _demote_other_defaults(self, profile: UptimeMonitorProfile) -> None:
        """Exactly one default per monitor type.

        Enforced here rather than by a partial unique index because "the default" is a product
        rule with a fallback chain behind it (:func:`profiles.pick_profile`), and a constraint
        would turn an ordinary "make this one the default" into a 409 the user has to resolve by
        unticking the other one first.
        """
        stmt = self.profiles.scoped_select().where(
            UptimeMonitorProfile.id != profile.id,
            UptimeMonitorProfile.monitor_type == profile.monitor_type,
            UptimeMonitorProfile.is_default.is_(True),
        )
        for other in (await self.ctx.session.execute(stmt)).scalars().all():
            other.is_default = False
        await self.ctx.session.flush()

    async def delete_profile(self, profile_id: uuid.UUID) -> None:
        """Delete a profile; monitors that followed it fall back to the tenant's default.

        The FK is ``SET NULL`` on purpose: ``NULL`` already means *inherit*, so a deleted profile
        degrades to "follow the default" rather than orphaning forty monitors.
        """
        profile = await self.profiles.get_or_404(profile_id)
        await self.activity.record("uptime_monitor_profile", profile.id, "deleted")
        await self.ctx.session.delete(profile)

    async def effective_settings(self, monitor: UptimeMonitor) -> dict[str, Any]:
        """The one resolution, used by the create form, the push, the drift check and reconcile."""
        profile = None
        if monitor.profile_id is not None:
            profile = await self.profiles.get_or_404(monitor.profile_id)
        else:
            profile = prof.pick_profile(await self.list_profiles(), monitor.monitor_type, None)
        return prof.resolve(
            {
                "interval_seconds": monitor.interval_seconds,
                "retries": monitor.retries,
            },
            profile,
        )

    # ---------------------------------------------------------------- monitors

    async def create_monitor(self, payload: UptimeMonitorCreate) -> UptimeMonitor:
        """Create here **and** at Uptime Kuma, in that order, and never half of it.

        The local row is written first and the push follows, so a failed push leaves a
        ``pending`` monitor an admin can retry — rather than a monitor at Kuma that schakl has no
        record of, which is the half nobody can clean up from this side.

        The anchor goes through :meth:`_resolve_anchor`, which is the same resolution
        ``link_monitor`` and ``update_monitor`` use (#366). Taking ``company_id`` from the payload
        and the anchor ids on trust was the shape #321 spent a whole issue removing from the sync
        path: a monitor created from a website page posts ``website_id`` and no client, and landed
        at ``company_id IS NULL`` — visible to staff outside that client's group and invisible to
        the client whose site it watches (§285, #266). Back through the other door, and this time
        for monitors we made ourselves.
        """
        instance = await self.instances.get_or_404(payload.instance_id)
        parent = (
            await self.monitors.get_or_404(payload.parent_id)
            if payload.parent_id is not None
            else None
        )
        kind, anchor_id, company_id = await self._resolve_anchor(payload)
        monitor = await self.monitors.create(
            instance_id=instance.id,
            name=payload.name,
            monitor_type=payload.monitor_type,
            target=payload.target,
            port=payload.port,
            interval_seconds=payload.interval_seconds,
            retries=payload.retries,
            parent_id=parent.id if parent is not None else None,
            profile_id=payload.profile_id,
            website_id=anchor_id if kind == "website" else None,
            domain_id=anchor_id if kind == "domain" else None,
            hosting_id=anchor_id if kind == "hosting" else None,
            company_id=company_id,
            active=payload.active,
            # Ours, not found: this is what makes a later difference *drift* rather than truth.
            adopted=False,
            sync_status=SyncStatus.PENDING.value,
        )
        await self.ctx.session.flush()
        await self.activity.record(MONITOR_ENTITY_TYPE, monitor.id, "created")
        await self._push(instance, monitor, parent=parent)
        return await self._settled(monitor)

    async def update_monitor(
        self, monitor_id: uuid.UUID, payload: UptimeMonitorUpdate
    ) -> UptimeMonitor:
        """Edit a monitor — and push **only** when something Uptime Kuma owns actually changed.

        The link fields are ours alone: which client a monitor belongs to and which website it
        watches on our side mean nothing at the far end. Pushing them anyway is not merely
        wasteful, it is the shape §3 exists to prevent — a Socket.IO connect, login, read and
        write per row, so attaching forty freshly-adopted monitors to their clients through the
        bulk edit held one request open for a minute and spent the instance's login budget
        saying nothing (`bulk.py`'s own docstring already claimed this write did not push).

        Which client a monitor belongs to is **derived** from what it was attached to, unless
        the caller said otherwise. Two copies of "whose monitor is this" is how the horizon
        starts disagreeing with the record: a monitor linked to a client's website while
        ``company_id`` stayed NULL is visible to staff outside that client's group and invisible
        to the client (#285, #266), and nothing on the screen would say so.
        """
        monitor = await self.monitors.get_or_404(monitor_id)
        audited = (*prof.DRIFT_FIELDS, *_LINK_FIELDS, "parent_id")
        before = {f: getattr(monitor, f) for f in audited}

        values = payload.model_dump(exclude_unset=True)
        for field, value in values.items():
            setattr(monitor, field, value)
        if any(f in values for f in _ANCHOR_FIELDS) and "company_id" not in values:
            monitor.company_id = await self._company_for_link(monitor)
        await self.ctx.session.flush()

        changes = {
            f: {"from": before[f], "to": getattr(monitor, f)}
            for f in audited
            if before[f] != getattr(monitor, f)
        }
        if changes:
            await self.activity.record(
                MONITOR_ENTITY_TYPE, monitor.id, "updated", payload={"changes": _plain(changes)}
            )
        if any(f not in _LINK_FIELDS for f in values):
            instance = await self.instances.get_or_404(monitor.instance_id)
            await self._push(instance, monitor)
        return await self._settled(monitor)

    # -------------------------------------------------------------------- links

    async def _company_for_link(self, monitor: UptimeMonitor) -> uuid.UUID | None:
        """Whose monitor this is, from whatever it is attached to. **One resolution** (#321).

        Most specific first: a website is a hostname somebody recorded, a domain is the zone it
        sits in, hosting is the box. Attached to nothing means ``NULL``, which is not "unknown"
        — it is *not client data*, the state the repository already reads as visible to
        restricted staff and hidden from a client login.
        """
        for kind, field in zip(_ANCHOR_KINDS, _ANCHOR_FIELDS, strict=True):
            anchor_id = getattr(monitor, field)
            if anchor_id is not None:
                resolved = await self._anchor_companies({(kind, anchor_id)})
                return resolved.get((kind, anchor_id))
        return None

    async def _resolve_anchor(
        self, payload: UptimeMonitorCreate
    ) -> tuple[str | None, uuid.UUID | None, uuid.UUID | None]:
        """The one anchor a new monitor gets, and whose it is (#366).

        **One anchor, not three columns** — ``UptimeMonitorLink``'s rule, applied to the create
        payload, which for compatibility still carries all three ids. The most specific wins, in
        ``_company_for_link``'s order, and the others are dropped rather than stored: a monitor
        claiming to watch one client's website *and* another client's hosting is a row no screen
        can render honestly, and ``company_id`` could only ever agree with one of them.

        The anchor is resolved through the horizon, so an id the caller cannot see is a **404**
        and not a silently-written link (§15: a refusal must not confirm that the row exists).
        Without it the create route would be the one way past the fence ``link_monitor`` and
        ``update_monitor`` both stand behind.

        An explicit ``company_id`` still wins, exactly as it does on the update path — *unless the
        caller said otherwise* is the same sentence in both places.
        """
        for kind, field in zip(_ANCHOR_KINDS, _ANCHOR_FIELDS, strict=True):
            anchor_id = getattr(payload, field)
            if anchor_id is None:
                continue
            resolved = await self._anchor_companies({(kind, anchor_id)})
            if (kind, anchor_id) not in resolved:
                raise AppError("not_found", "errors.not_found", status_code=404)
            company_id = (
                payload.company_id
                if payload.company_id is not None
                else resolved[(kind, anchor_id)]
            )
            return kind, anchor_id, company_id
        return None, None, payload.company_id

    async def _anchor_companies(
        self, anchors: set[tuple[str, uuid.UUID]]
    ) -> dict[tuple[str, uuid.UUID], uuid.UUID | None]:
        """Resolve a batch of anchors to their clients — **at most one query per kind**.

        Absence from the answer means *not visible to this caller*, never "has no client": a
        hosting account with no client is a real state and comes back as an explicit ``None``.
        Conflating the two would turn shared infrastructure into a 404.
        """
        resolved: dict[tuple[str, uuid.UUID], uuid.UUID | None] = {}
        for kind in matching.LINKABLE:
            ids = [anchor_id for (k, anchor_id) in anchors if k == kind]
            if not ids:
                continue
            rows = await self.ctx.session.execute(
                matching.anchor_query(kind, self.ctx.org.id, self.ctx.company_scope, ids)
            )
            for row in rows:
                resolved[(kind, row[0])] = row[1]
        return resolved

    def _write_link(
        self,
        monitor: UptimeMonitor,
        *,
        kind: str | None,
        anchor_id: uuid.UUID | None,
        company_id: uuid.UUID | None,
    ) -> None:
        """Set exactly one anchor and clear the others. The only place link columns are written.

        Clearing the others is the point rather than tidiness: a monitor that claims to watch
        one client's website *and* another client's hosting is a row no screen can render
        honestly, and `company_id` could only agree with one of them.
        """
        for candidate_kind, field in zip(_ANCHOR_KINDS, _ANCHOR_FIELDS, strict=True):
            setattr(monitor, field, anchor_id if candidate_kind == kind else None)
        monitor.company_id = company_id

    async def link_monitor(
        self, monitor_id: uuid.UUID, payload: UptimeMonitorLink
    ) -> UptimeMonitor:
        """Attach one found monitor to the website, domain or hosting it watches — or detach it.

        **Writes nothing to Uptime Kuma.** Which client a monitor belongs to is a fact about our
        administration; the far end has no opinion on it and no field for it. That is also what
        makes this route safe to hand an ordinary employee working through a freshly adopted
        instance: the worst outcome is a wrong row here, not a changed check on a client's site.
        """
        monitor = await self.monitors.get_or_404(monitor_id)
        before = {f: getattr(monitor, f) for f in _LINK_FIELDS}

        if payload.entity_type is None:
            self._write_link(monitor, kind=None, anchor_id=None, company_id=None)
        else:
            key = (payload.entity_type, payload.entity_id)
            resolved = await self._anchor_companies({key})
            if key not in resolved:
                # 404 and not 403: an anchor outside this caller's horizon must not be
                # confirmed to exist by the shape of the refusal (§15).
                raise AppError("not_found", "errors.not_found", status_code=404)
            self._write_link(
                monitor,
                kind=payload.entity_type,
                anchor_id=payload.entity_id,
                company_id=resolved[key],
            )

        changes = {
            f: {"from": before[f], "to": getattr(monitor, f)}
            for f in _LINK_FIELDS
            if before[f] != getattr(monitor, f)
        }
        if changes:
            await self.activity.record(
                MONITOR_ENTITY_TYPE, monitor.id, "updated", payload={"changes": _plain(changes)}
            )
        return await self._settled(monitor)

    async def apply_links(self, instance_id: uuid.UUID) -> UptimeLinkApplyResult:
        """Confirm every **unambiguous** proposal on one instance, and count what it left.

        The button an agency presses once after adopting an instance with two hundred monitors.
        It applies only what has exactly one candidate; the ambiguous ones are reported as
        ``skipped``, never resolved by picking the first — that is the decision docs/UPTIME.md §9
        reserves for a person, and doing it in bulk would be doing it two hundred times.

        Batched throughout (docs/PERFORMANCE.md): one read of the proposals, one count of the
        ambiguous ones, and one query per anchor kind — not one per monitor.
        """
        instance = await self.instances.get_or_404(instance_id)
        stmt = self.monitors.scoped_select().where(
            *_monitor_filters(instance_id=instance.id, link_status=matching.MATCHED)
        )
        rows = list((await self.ctx.session.execute(stmt)).scalars().all())
        skipped = int(
            (
                await self.ctx.session.execute(
                    self.monitors.scoped_count_select().where(
                        *_monitor_filters(instance_id=instance.id, link_status=matching.AMBIGUOUS)
                    )
                )
            ).scalar()
            or 0
        )

        anchors: set[tuple[str, uuid.UUID]] = set()
        for row in rows:
            anchor = _sole_candidate(row)
            if anchor is not None:
                anchors.add(anchor)
        resolved = await self._anchor_companies(anchors)

        linked = 0
        for row in rows:
            anchor = _sole_candidate(row)
            # A candidate whose anchor has since been deleted, or moved out of this caller's
            # horizon, is skipped rather than written as a dangling id: the proposal is an
            # observation from the last sync, and the anchor is re-read at the moment it counts.
            if anchor is None or anchor not in resolved:
                skipped += 1
                continue
            self._write_link(
                row, kind=anchor[0], anchor_id=anchor[1], company_id=resolved[anchor]
            )
            await self.activity.record(
                MONITOR_ENTITY_TYPE,
                row.id,
                "updated",
                payload={"changes": {f"{anchor[0]}_id": {"from": None, "to": str(anchor[1])}}},
            )
            linked += 1
        await self.ctx.session.flush()
        return UptimeLinkApplyResult(linked=linked, skipped=skipped)

    async def set_paused(self, monitor_id: uuid.UUID, *, paused: bool) -> UptimeMonitor:
        """Pause or resume. Its own permission, and deliberately not drift.

        Silencing an alert during a planned migration is an ordinary thing to ask of an ordinary
        employee; repointing a monitor is not. And a monitor paused in Kuma during an incident
        must not read as a configuration conflict somebody has to resolve.

        Pausing a **group** silences every monitor beneath it, because that is what Uptime Kuma
        does with a paused parent — the children's own ``active`` stays true here and they stop
        being checked there. Said out loud on the screen (`uptime.group.pause_cascade`) rather
        than modelled, since inventing a per-child row to mirror it would be this module
        claiming a state the far end does not have.
        """
        monitor = await self.monitors.get_or_404(monitor_id)
        instance = await self.instances.get_or_404(monitor.instance_id)
        if monitor.kuma_monitor_id is None:
            raise AppError("validation_error", "errors.uptime_not_pushed", status_code=409)

        kuma_id = monitor.kuma_monitor_id

        def _work(client: UptimeKumaClient) -> None:
            client.authenticate(_token(instance))
            if paused:
                client.pause_monitor(kuma_id)
            else:
                client.resume_monitor(kuma_id)

        await self._in_kuma(instance, _work)
        await self._reload(monitor)
        monitor.active = not paused
        await self.activity.record(
            MONITOR_ENTITY_TYPE, monitor.id, "paused" if paused else "resumed"
        )
        return await self._settled(monitor)

    async def delete_monitor(self, monitor_id: uuid.UUID, *, at_kuma: bool) -> None:
        """Delete the local row, and optionally the monitor at Uptime Kuma.

        ``at_kuma`` is an explicit choice and defaults to *no*, for the reason deleting an
        instance touches nothing: "stop tracking this here" and "stop watching this client's
        site" are different decisions, and the destructive one is never the side effect of the
        other. When it is asked for, it takes the id this module stored — never a name match.

        **A group with children is refused** (docs/UPTIME.md §7). The self-FK is ``SET NULL``,
        so deleting one locally would silently un-nest every monitor beneath it here while Kuma
        keeps them exactly where they were — a hierarchy that quietly disagrees with the far end
        and nothing to say when. Whether Kuma itself would delete the children is *its* answer
        to give, and discovering it on a client's live monitoring is not how to find out.
        """
        monitor = await self.monitors.get_or_404(monitor_id)
        if monitor.monitor_type == GROUP_TYPE:
            children = int(
                (
                    await self.ctx.session.execute(
                        self.monitors.scoped_count_select().where(
                            UptimeMonitor.parent_id == monitor.id
                        )
                    )
                ).scalar()
                or 0
            )
            if children:
                raise AppError(
                    "conflict", "errors.uptime_group_has_children", status_code=409
                )
        if at_kuma and monitor.kuma_monitor_id is not None:
            instance = await self.instances.get_or_404(monitor.instance_id)
            kuma_id = monitor.kuma_monitor_id

            def _work(client: UptimeKumaClient) -> None:
                client.authenticate(_token(instance))
                client.delete_monitor(kuma_id)

            await self._in_kuma(instance, _work)
            await self._reload(monitor)
        await self.activity.record(
            MONITOR_ENTITY_TYPE, monitor.id, "deleted", payload={"at_kuma": at_kuma}
        )
        await self.ctx.session.delete(monitor)

    # ------------------------------------------------------- pushing and drift

    async def _push(
        self,
        instance: UptimeInstance,
        monitor: UptimeMonitor,
        *,
        parent: UptimeMonitor | None = None,
    ) -> None:
        """Write this monitor's decided state to Uptime Kuma.

        On an existing monitor this is **read-then-write**: the payload starts from a fresh
        ``getMonitor`` and only the keys we own are written over it. A live 2.5.0 returns 119
        keys against the 16 a create sends, so a payload rebuilt from the fields this module
        models would silently reset a hundred of them — including every field belonging to a
        monitor type we do not know about.
        """
        settings = await self.effective_settings(monitor)
        parent_kuma = parent.kuma_monitor_id if parent is not None else None
        if parent is None and monitor.parent_id is not None:
            parent_row = await self.monitors.get_or_404(monitor.parent_id)
            parent_kuma = parent_row.kuma_monitor_id

        fields = _kuma_fields(monitor, settings, parent_kuma)
        existing_id = monitor.kuma_monitor_id
        token = _token(instance)

        def _work(client: UptimeKumaClient) -> tuple[int, dict[str, Any]]:
            client.authenticate(token)
            if existing_id is None:
                new_id = client.add_monitor(fields)
            else:
                new_id = existing_id
                observed = client.get_monitor(new_id)
                client.edit_monitor(merge_monitor(observed, {**fields, "id": new_id}))
            return new_id, client.get_monitor(new_id)

        try:
            kuma_id, observed = await self._in_kuma(instance, _work)
        except kuma_errors.UptimeKumaError as exc:
            await self._reload(monitor)
            monitor.sync_status = SyncStatus.ERROR.value
            monitor.last_error = str(exc)[:500]
            await self.ctx.session.flush()
            raise AppError("upstream_error", error_key(exc), status_code=502) from exc

        await self._reload(monitor, instance)
        monitor.kuma_monitor_id = kuma_id
        monitor.remote_snapshot = redact_monitor(observed, salt=instance.secret_salt)
        monitor.last_observed_at = datetime.now(UTC)
        monitor.sync_status = SyncStatus.ACTIVE.value
        monitor.drift_fields = []
        monitor.last_error = None
        await self.ctx.session.flush()

    async def _observed_parent(self, monitor: UptimeMonitor) -> uuid.UUID | None:
        """Our row for the group Uptime Kuma says this monitor sits in, if we mirror it.

        Goes through the repository like every other read here, so a group outside the caller's
        horizon resolves to nothing rather than being written onto a row they can see.
        """
        parent = (monitor.remote_snapshot or {}).get("parent")
        if not isinstance(parent, int):
            return None
        stmt = (
            self.monitors.scoped_select()
            .with_only_columns(UptimeMonitor.id)
            .where(
                UptimeMonitor.instance_id == monitor.instance_id,
                UptimeMonitor.kuma_monitor_id == parent,
            )
            .limit(1)
        )
        return (await self.ctx.session.execute(stmt)).scalar_one_or_none()

    async def reconcile(self, monitor_id: uuid.UUID, payload: UptimeReconcile) -> UptimeMonitor:
        """Resolve a drift — in **either** direction, which is the whole point.

        An agency editing a monitor in Kuma because that screen was closer to hand is the normal
        case, not the deviant one. A reconcile that could only overwrite would teach people to
        stop using the tool they already had, so ``adopt`` copies Uptime Kuma's state into ours
        and ``push`` sends ours to Kuma.
        """
        monitor = await self.monitors.get_or_404(monitor_id)
        instance = await self.instances.get_or_404(monitor.instance_id)

        if payload.direction == "push":
            await self._push(instance, monitor)
            await self.activity.record(MONITOR_ENTITY_TYPE, monitor.id, "drift_pushed")
            return await self._settled(monitor)

        snapshot = monitor.remote_snapshot or {}
        for field in prof.DRIFT_FIELDS:
            value = prof.observed_value(field, snapshot, monitor.monitor_type)
            if value is not None:
                setattr(monitor, field, _coerce(field, value))
        # The group moved too, if it moved (#321). Adopting five fields and leaving the sixth
        # would clear the drift flag while the disagreement it named was still there — and the
        # next sync would raise it again, which reads as a reconcile that did not work.
        if "parent_id" in (monitor.drift_fields or []):
            monitor.parent_id = await self._observed_parent(monitor)
        monitor.drift_fields = []
        monitor.sync_status = SyncStatus.ACTIVE.value
        await self.activity.record(MONITOR_ENTITY_TYPE, monitor.id, "drift_adopted")
        return await self._settled(monitor)


def _token(instance: UptimeInstance) -> str:
    if not instance.token_encrypted:
        raise AppError("validation_error", "errors.uptime_not_enrolled", status_code=409)
    return decrypt(instance.token_encrypted)


def _coerce(field: str, value: Any) -> Any:
    if field in ("port", "interval_seconds", "retries"):
        return _int_or_none(value)
    return value


def _kuma_fields(
    monitor: UptimeMonitor, settings: dict[str, Any], parent_kuma: int | None
) -> dict[str, Any]:
    """Our decided state as the keys Uptime Kuma uses.

    Only the keys this module owns. Everything else on an existing monitor is preserved by
    ``merge_monitor``, and everything else on a new one takes Kuma's own default — which is
    what an agency would have got by making it in Kuma's UI.
    """
    fields: dict[str, Any] = {
        "type": monitor.monitor_type,
        "name": monitor.name,
        "interval": settings["interval_seconds"],
        "maxretries": settings["retries"],
        "retryInterval": settings["retry_interval_seconds"],
        "resendInterval": settings["resend_interval"],
        "accepted_statuscodes": settings["accepted_status_codes"],
        "upsideDown": settings["upside_down"],
        "parent": parent_kuma,
    }
    if monitor.monitor_type != "group":
        # A group watches nothing itself, and sending it a target is how a "group" ends up with
        # a URL nobody meant to give it.
        fields[prof.target_field(monitor.monitor_type)] = monitor.target
        if monitor.port is not None:
            fields["port"] = monitor.port
    if monitor.monitor_type in ("http", "keyword", "json-query", "real-browser"):
        fields |= {
            "method": settings["method"],
            "maxredirects": settings["max_redirects"],
            "timeout": settings["timeout_seconds"],
            "expiryNotification": settings["expiry_notification"],
            "ignoreTls": settings["ignore_tls"],
        }
    return fields
