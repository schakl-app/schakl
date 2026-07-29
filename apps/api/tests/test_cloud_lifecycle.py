"""Per-org end date: warn → suspend → terminate (epic #199).

The property this file exists to protect is that **nothing is destroyed by accident**. An org
without an end date is untouchable; the sweep is off unless switched on; the purge needs a
second switch; and a failure anywhere before the purge leaves the org intact and recoverable.
Each of those is a separate test, because each is a separate way to lose a customer's data.
"""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.config import settings
from app.core.cloud import cloudflare as cf
from app.core.cloud import lifecycle
from app.core.models import Org, OrgStatus
from app.core.storage.backend import get_storage
from app.core.storage.models import StoredFile
from app.db import async_session_maker, set_current_org
from tests.conftest import Tenant, auth_cookie, make_tenant
from tests.test_cloud import _FakeCloudflare, make_instance_owner, mint_instance_key

NOW = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)


@pytest.fixture
def lifecycle_on(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "deployment", "cloud")
    monkeypatch.setattr(settings, "instance_admin_enabled", True)
    monkeypatch.setattr(settings, "cloud_lifecycle_enabled", True)
    monkeypatch.setattr(settings, "cloud_lifecycle_destructive", True)
    monkeypatch.setattr(settings, "cloud_grace_days", 14)
    monkeypatch.setattr(settings, "cloud_retention_days", 30)
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))


async def _set_end(tenant: Tenant, ends_at: datetime | None, **kwargs) -> None:
    async with async_session_maker() as session:
        org = await session.get(Org, tenant.org.id)
        org.ends_at = ends_at
        for key, value in kwargs.items():
            setattr(org, key, value)
        await session.commit()


async def _reload(org_id: uuid.UUID) -> Org | None:
    async with async_session_maker() as session:
        return await session.get(Org, org_id)


async def _sweep(now: datetime) -> dict[str, int]:
    async with async_session_maker() as session:
        return await lifecycle.sweep(session, now=now)


# --------------------------------------------------------------------------- #
# The safety rails
# --------------------------------------------------------------------------- #
async def test_an_org_without_an_end_date_is_never_touched(lifecycle_on) -> None:
    """NULL means unlimited. This is the default for every org that never sets a date, and for
    every org that existed before the feature — so it is the case that must never move."""
    tenant = await make_tenant("lc-unlimited")
    counts = await _sweep(NOW + timedelta(days=3650))
    assert counts == {"warned": 0, "suspended": 0, "terminated": 0}
    org = await _reload(tenant.org.id)
    assert org is not None and org.status == OrgStatus.ACTIVE.value
    assert org.lifecycle_stage == lifecycle.STAGE_ACTIVE


async def test_sweep_does_nothing_while_disabled(lifecycle_on, monkeypatch) -> None:
    monkeypatch.setattr(settings, "cloud_lifecycle_enabled", False)
    tenant = await make_tenant("lc-off")
    await _set_end(tenant, NOW - timedelta(days=365))
    assert await _sweep(NOW) == {"warned": 0, "suspended": 0, "terminated": 0}
    assert (await _reload(tenant.org.id)).status == OrgStatus.ACTIVE.value


async def test_a_future_end_date_does_nothing_yet(lifecycle_on) -> None:
    tenant = await make_tenant("lc-future")
    await _set_end(tenant, NOW + timedelta(days=10))
    assert await _sweep(NOW) == {"warned": 0, "suspended": 0, "terminated": 0}
    assert (await _reload(tenant.org.id)).lifecycle_stage == lifecycle.STAGE_ACTIVE


# --------------------------------------------------------------------------- #
# The ladder
# --------------------------------------------------------------------------- #
async def test_warning_stage_keeps_the_org_usable(lifecycle_on) -> None:
    tenant = await make_tenant("lc-warn")
    await _set_end(tenant, NOW - timedelta(days=1))

    assert (await _sweep(NOW))["warned"] == 1
    org = await _reload(tenant.org.id)
    assert org.lifecycle_stage == lifecycle.STAGE_WARNING
    # Warned, not punished: full access throughout the grace window.
    assert org.status == OrgStatus.ACTIVE.value
    assert org.lifecycle_notified_at is not None


async def test_sweep_is_idempotent_within_a_window(lifecycle_on) -> None:
    """A daily cron re-runs over the same orgs. The second pass must not re-notify."""
    tenant = await make_tenant("lc-idem")
    await _set_end(tenant, NOW - timedelta(days=1))

    assert (await _sweep(NOW))["warned"] == 1
    first = (await _reload(tenant.org.id)).lifecycle_notified_at
    assert (await _sweep(NOW + timedelta(hours=6)))["warned"] == 0
    assert (await _reload(tenant.org.id)).lifecycle_notified_at == first


async def test_grace_expiry_suspends_but_keeps_the_data(lifecycle_on) -> None:
    tenant = await make_tenant("lc-susp")
    await _set_end(tenant, NOW - timedelta(days=20))  # past the 14-day grace

    assert (await _sweep(NOW))["suspended"] == 1
    org = await _reload(tenant.org.id)
    assert org.status == OrgStatus.SUSPENDED.value
    assert org.lifecycle_stage == lifecycle.STAGE_SUSPENDED
    # The last recoverable state: blocked, but every row still there.
    assert org.deleted_at is None


async def test_per_org_windows_override_the_instance_defaults(lifecycle_on) -> None:
    tenant = await make_tenant("lc-override")
    await _set_end(tenant, NOW - timedelta(days=3), grace_days=2, retention_days=5)
    org = await _reload(tenant.org.id)
    assert lifecycle.grace_days(org) == 2
    assert lifecycle.retention_days(org) == 5
    # 3 days past the end with a 2-day grace → already suspended, not merely warned.
    assert (await _sweep(NOW))["suspended"] == 1


async def test_moving_the_end_date_forward_rearms_the_warning(lifecycle_on) -> None:
    """A customer who renews must stop being warned — and must be warned again next time."""
    tenant = await make_tenant("lc-renew")
    await _set_end(tenant, NOW - timedelta(days=1))
    await _sweep(NOW)
    assert (await _reload(tenant.org.id)).lifecycle_stage == lifecycle.STAGE_WARNING

    async with async_session_maker() as session:
        org = await session.get(Org, tenant.org.id)
        await lifecycle.set_lifecycle(
            session, tenant.user, org, ends_at=NOW + timedelta(days=365)
        )
        await session.commit()

    org = await _reload(tenant.org.id)
    assert org.lifecycle_stage == lifecycle.STAGE_ACTIVE
    assert org.lifecycle_notified_at is None
    # …and the warning fires again once the new date passes.
    assert (await _sweep(NOW + timedelta(days=366)))["warned"] == 1


async def test_clearing_the_end_date_makes_it_unlimited_again(lifecycle_on) -> None:
    tenant = await make_tenant("lc-clear")
    await _set_end(tenant, NOW - timedelta(days=1))
    await _sweep(NOW)

    async with async_session_maker() as session:
        org = await session.get(Org, tenant.org.id)
        await lifecycle.set_lifecycle(session, tenant.user, org, ends_at=None)
        await session.commit()

    assert await _sweep(NOW + timedelta(days=3650)) == {
        "warned": 0, "suspended": 0, "terminated": 0
    }


# --------------------------------------------------------------------------- #
# Termination
# --------------------------------------------------------------------------- #
async def _seed_blob(org_id: uuid.UUID) -> uuid.UUID:
    file_id = uuid.uuid4()
    get_storage().put(f"{org_id}/{file_id}", io.BytesIO(b"tenant bytes"))
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        session.add(
            StoredFile(
                id=file_id, org_id=org_id, backend="local",
                storage_key=f"{org_id}/{file_id}", filename="x.txt",
                content_type="text/plain", size_bytes=12,
            )
        )
        await session.commit()
    return file_id


def _archive_dir(org_id: uuid.UUID) -> Path:
    return Path(settings.storage_path) / lifecycle.ARCHIVE_PREFIX / str(org_id)


def _tenant_dir(org_id: uuid.UUID) -> Path:
    return Path(settings.storage_path) / str(org_id)


async def test_termination_archives_then_purges_and_reclaims_bytes(lifecycle_on) -> None:
    tenant = await make_tenant("lc-term")
    org_id = tenant.org.id
    file_id = await _seed_blob(org_id)
    assert get_storage().open(f"{org_id}/{file_id}").read() == b"tenant bytes"
    await _set_end(tenant, NOW - timedelta(days=60))  # past grace + retention

    assert (await _sweep(NOW))["terminated"] == 1

    # The rows are gone…
    assert await _reload(org_id) is None
    # …the blob that definitely existed a moment ago is gone, and so is the whole prefix…
    with pytest.raises(FileNotFoundError):
        get_storage().open(f"{org_id}/{file_id}")
    assert not _tenant_dir(org_id).exists()
    # …and the archive survives, outside the tenant key space the purge just emptied.
    archives = list(_archive_dir(org_id).iterdir())
    assert len(archives) == 1 and archives[0].suffix == ".zip"
    assert archives[0].stat().st_size > 0


async def test_destructive_off_archives_but_never_purges(lifecycle_on, monkeypatch) -> None:
    """The intended first deployment: real warnings and suspensions, nothing destroyed."""
    monkeypatch.setattr(settings, "cloud_lifecycle_destructive", False)
    tenant = await make_tenant("lc-safe")
    org_id = tenant.org.id
    file_id = await _seed_blob(org_id)
    await _set_end(tenant, NOW - timedelta(days=60))

    assert (await _sweep(NOW))["terminated"] == 0
    org = await _reload(org_id)
    assert org is not None
    # Soft-deleted and archived, so the operator can see exactly what would have happened.
    assert org.status == OrgStatus.DELETED.value
    assert org.exported_at is not None
    assert list(_archive_dir(org_id).iterdir())
    # The bytes are untouched: nothing irreversible happened.
    assert get_storage().open(f"{org_id}/{file_id}").read() == b"tenant bytes"
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        rows = await session.execute(select(StoredFile.id).where(StoredFile.org_id == org_id))
        assert [r for (r,) in rows] == [file_id]


async def test_withheld_termination_archives_once_not_nightly(
    lifecycle_on, monkeypatch
) -> None:
    """With the destructive switch off the sweep still runs daily. Re-archiving each time would
    write one full copy of the org per night for as long as the switch stays off."""
    monkeypatch.setattr(settings, "cloud_lifecycle_destructive", False)
    tenant = await make_tenant("lc-once")
    org_id = tenant.org.id
    await _seed_blob(org_id)
    await _set_end(tenant, NOW - timedelta(days=60))

    # Count the archive step itself. Counting files on disk would not catch this: the key is
    # timestamped, so three sweeps inside one second would overwrite one another and look fine.
    real = lifecycle._archive_to_storage
    calls = {"n": 0}

    async def counting(session, org):
        calls["n"] += 1
        return await real(session, org)

    monkeypatch.setattr(lifecycle, "_archive_to_storage", counting)

    await _sweep(NOW)
    assert calls["n"] == 1
    await _sweep(NOW + timedelta(days=1))
    await _sweep(NOW + timedelta(days=2))
    assert calls["n"] == 1, "a withheld termination re-archived the org on a later sweep"
    assert len(list(_archive_dir(org_id).iterdir())) == 1


async def test_a_failed_archive_never_purges(lifecycle_on, monkeypatch) -> None:
    """The whole point of the ordering: no export, no destruction. The org stays soft-deleted
    and the next sweep retries — a soft-deleted org resolves for nobody, so retrying is inert."""
    tenant = await make_tenant("lc-failsafe")
    org_id = tenant.org.id
    await _set_end(tenant, NOW - timedelta(days=60))

    async def boom(*args, **kwargs):
        raise OSError("archive storage unavailable")

    monkeypatch.setattr(lifecycle, "_archive_to_storage", boom)
    assert (await _sweep(NOW))["terminated"] == 0

    org = await _reload(org_id)
    assert org is not None  # NOT purged
    assert org.exported_at is None


async def test_termination_releases_cloudflare_records(lifecycle_on, monkeypatch) -> None:
    fake = _FakeCloudflare()
    monkeypatch.setattr(settings, "cloud_cf_api_token", "tok")
    monkeypatch.setattr(settings, "cloud_cf_zone_id", "zone-123")
    monkeypatch.setattr(cf, "_transport", httpx.MockTransport(fake.handler))
    fake.records["ch9"] = {"id": "ch9", "hostname": "crm.klant.test"}
    fake.dns["dns9"] = {"id": "dns9", "name": "lc-cf.localhost", "type": "CNAME"}

    tenant = await make_tenant("lc-cf")
    await _set_end(
        tenant, NOW - timedelta(days=60), cf_hostname_id="ch9", cf_dns_record_id="dns9"
    )

    assert (await _sweep(NOW))["terminated"] == 1
    assert fake.records == {}
    assert fake.dns == {}


async def test_one_bad_org_does_not_stop_the_sweep(lifecycle_on, monkeypatch) -> None:
    good = await make_tenant("lc-good")
    bad = await make_tenant("lc-bad")
    await _set_end(good, NOW - timedelta(days=1))
    await _set_end(bad, NOW - timedelta(days=1))

    real = lifecycle._notify
    calls = {"n": 0}

    async def flaky(session, org, stage):
        calls["n"] += 1
        if org.slug == "lc-bad":
            raise RuntimeError("notification exploded")
        await real(session, org, stage)

    monkeypatch.setattr(lifecycle, "_notify", flaky)
    counts = await _sweep(NOW)
    assert calls["n"] == 2
    assert counts["warned"] == 1  # the healthy org still advanced
    assert (await _reload(good.org.id)).lifecycle_stage == lifecycle.STAGE_WARNING


# --------------------------------------------------------------------------- #
# The surfaces that set it
# --------------------------------------------------------------------------- #
async def test_console_sets_and_clears_the_end_date(client_for, lifecycle_on) -> None:
    admin = await make_tenant("lc-console")
    await make_instance_owner(admin)
    headers = await auth_cookie(admin.user)
    target = await make_tenant("lc-target")
    ends = (NOW + timedelta(days=30)).isoformat()

    async with client_for(admin.host) as client:
        # Reading it needs no service PIN: an end date is billing state, not tenant content.
        read = await client.get(
            f"/api/v1/instance/orgs/{target.org.id}/lifecycle", headers=headers
        )
        assert read.status_code == 200
        assert read.json()["ends_at"] is None

        saved = await client.patch(
            f"/api/v1/instance/orgs/{target.org.id}/lifecycle",
            headers=headers,
            json={"ends_at": ends, "grace_days": 7, "retention_days": 14},
        )
        assert saved.status_code == 200
        body = saved.json()
        assert body["grace_days"] == 7
        # The computed instants come back, so the console never re-derives the schedule.
        assert datetime.fromisoformat(body["suspends_at"]) == NOW + timedelta(days=37)
        assert datetime.fromisoformat(body["terminates_at"]) == NOW + timedelta(days=51)

        cleared = await client.patch(
            f"/api/v1/instance/orgs/{target.org.id}/lifecycle",
            headers=headers,
            json={"ends_at": None},
        )
        assert cleared.json()["ends_at"] is None
        assert cleared.json()["terminates_at"] is None


async def test_provisioning_key_can_set_the_end_date(client_for, lifecycle_on) -> None:
    """The billing system drives it, separately from the plan: a plan says how an org is
    billed, an end date says until when it exists."""
    admin = await make_tenant("lc-prov")
    await make_instance_owner(admin)
    headers = await auth_cookie(admin.user)

    async with client_for(admin.host) as client:
        secret = await mint_instance_key(client_for, (client, headers))
        key_headers = {"X-API-Key": secret}
        created = await client.post(
            "/api/v1/instance/provisioning/orgs",
            headers=key_headers,
            json={
                "name": "Fixed Term", "slug": "fixed-term",
                "owner_email": "boss@fixed.example", "plan": "standard",
            },
        )
        assert created.status_code == 201

        updated = await client.patch(
            "/api/v1/instance/provisioning/orgs/fixed-term/lifecycle",
            headers=key_headers,
            json={"ends_at": (NOW + timedelta(days=90)).isoformat()},
        )
        assert updated.status_code == 200

    async with async_session_maker() as session:
        org = await session.scalar(select(Org).where(Org.slug == "fixed-term"))
        assert org.ends_at == NOW + timedelta(days=90)
        assert org.plan == "standard"  # the plan is untouched


async def test_lifecycle_surface_is_404_on_self_host(client_for, monkeypatch) -> None:
    monkeypatch.setattr(settings, "instance_admin_enabled", True)
    admin = await make_tenant("lc-selfhost")
    await make_instance_owner(admin)
    headers = await auth_cookie(admin.user)
    async with client_for(admin.host) as client:
        gone = await client.get(
            f"/api/v1/instance/orgs/{admin.org.id}/lifecycle", headers=headers
        )
        assert gone.status_code == 404


async def test_warning_reaches_the_tenant_through_meta(client_for, lifecycle_on) -> None:
    tenant = await make_tenant("lc-banner")
    await _set_end(tenant, NOW - timedelta(days=1))
    await _sweep(NOW)

    async with client_for(tenant.host) as client:
        meta = await client.get("/api/v1/meta/tenant")
        assert meta.status_code == 200
        # The banner needs the deletion date, not the end date: "what happens and when".
        assert meta.json()["ends_warning_until"] is not None

    # Silent once suspended — the suspension screen is the message from then on.
    await _set_end(tenant, NOW - timedelta(days=20))
    await _sweep(NOW)
    async with client_for(tenant.host) as client:
        assert (await client.get("/api/v1/meta/tenant")).json()["ends_warning_until"] is None


async def test_meta_is_silent_for_an_unlimited_org(client_for, lifecycle_on) -> None:
    tenant = await make_tenant("lc-quiet")
    async with client_for(tenant.host) as client:
        assert (await client.get("/api/v1/meta/tenant")).json()["ends_warning_until"] is None


async def test_termination_batch_is_bounded(lifecycle_on, monkeypatch) -> None:
    """A misconfigured end date must not purge the whole instance in one run."""
    monkeypatch.setattr(settings, "cloud_lifecycle_batch", 1)
    first = await make_tenant("lc-batch-a")
    second = await make_tenant("lc-batch-b")
    await _set_end(first, NOW - timedelta(days=61))
    await _set_end(second, NOW - timedelta(days=60))

    assert (await _sweep(NOW))["terminated"] == 1
    survivors = [await _reload(first.org.id), await _reload(second.org.id)]
    assert sum(org is not None for org in survivors) == 1
