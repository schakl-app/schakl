"""Cloud entitlement: the tenant's **plan** decides, not the operator's licence key.

The bug this pins: ``orgs.plan`` reached only the trial-suspension cron, so an org the
operator had explicitly set to ``unlimited`` still went read-only across every licensed
module the moment the instance-wide licence key lapsed or was never installed — and the 402
told the tenant that *their* licence had expired, naming an artefact they do not hold and a
fix (Instellingen → Licentie) they cannot reach.

The self-hosted half must not move: there the instance key is the only authority there is,
and every assertion about it lives in ``test_entitlements.py``. What is new here is the
*split* — same instance state, two postures, two answers — so each test below runs the same
unlicensed instance twice where the difference is the point.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy import text as sql_text

from app.config import settings
from app.core.entitlements.service import (
    OrgPlan,
    invalidate_plan_cache,
    refusal_for,
    sku_cron_enabled,
)
from app.db import async_session_maker
from tests.conftest import auth_cookie, make_tenant
from tests.test_entitlements import _b64url, _reset_instance_license


@pytest.fixture
def cloud_mode(monkeypatch) -> None:
    monkeypatch.setattr(settings, "deployment", "cloud")


@pytest.fixture
async def license_key(monkeypatch):
    """An ephemeral signing key, and a clean instance row after. Defined here rather than
    imported from ``test_entitlements`` because importing a fixture makes every test function
    that names it read as a redefinition (ruff F811) — the six lines are cheaper than the
    suppressions, and this file only needs the *unlicensed* half of it."""
    private = Ed25519PrivateKey.generate()
    public_b64 = _b64url(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    monkeypatch.setattr(settings, "license_public_key", public_b64)
    await _reset_instance_license()
    yield private
    await _reset_instance_license()


@pytest.fixture(autouse=True)
def _clear_plan_cache():
    """The plan is memoised per host for a minute; a test that sets one must not read the
    previous test's answer for the same slug."""
    invalidate_plan_cache()
    yield
    invalidate_plan_cache()


async def _set_plan(slug: str, plan: str, *, trial_ends_at: datetime | None = None) -> None:
    async with async_session_maker() as session:
        await session.execute(
            sql_text(
                "UPDATE orgs SET plan = :plan, trial_ends_at = :ends WHERE slug = :slug"
            ),
            {"plan": plan, "ends": trial_ends_at, "slug": slug},
        )
        await session.commit()
    invalidate_plan_cache()


# --------------------------------------------------------------------------- #
# OrgPlan.live() — the whole lifecycle rule, without a request
# --------------------------------------------------------------------------- #
def test_unlimited_and_standard_are_live_forever() -> None:
    assert OrgPlan(plan="unlimited", trial_ends_at=None).live()
    assert OrgPlan(plan="standard", trial_ends_at=None).live()


def test_trial_is_live_until_its_end_date() -> None:
    future = datetime.now(UTC) + timedelta(days=1)
    past = datetime.now(UTC) - timedelta(days=1)
    assert OrgPlan(plan="trial", trial_ends_at=future).live()
    assert not OrgPlan(plan="trial", trial_ends_at=past).live()


def test_an_unarmed_trial_is_running_not_lapsed() -> None:
    """A freshly provisioned org whose clock has not been set yet must not be locked out of
    the product it is trialling — the failure direction that must not exist."""
    assert OrgPlan(plan="trial", trial_ends_at=None).live()


def test_no_org_and_unknown_plans_are_not_live() -> None:
    # None is the console apex, where callers fall back to the instance licence instead.
    assert not OrgPlan(plan=None, trial_ends_at=None).live()
    assert not OrgPlan(plan="cancelled", trial_ends_at=None).live()


# --------------------------------------------------------------------------- #
# The write gate
# --------------------------------------------------------------------------- #
async def test_unlimited_org_keeps_writing_on_an_unlicensed_cloud_instance(
    client_for, license_key, cloud_mode
) -> None:
    """The reported bug, end to end: no instance licence, bootstrap window long closed, org on
    ``unlimited`` — and a licensed module's mutation must still succeed."""
    t = await make_tenant("cloud-unlimited")
    await _set_plan("cloud-unlimited", "unlimited")
    await _reset_instance_license(grace_started_days_ago=999)
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        r = await c.post(
            "/api/v1/leave/types",
            json={"key": "vacation", "label_i18n": {"nl": "Vakantie", "en": "Vacation"}},
            headers=headers,
        )
        assert r.status_code == 201, r.text


async def test_same_instance_still_refuses_on_self_host(client_for, license_key) -> None:
    """The control for the test above: identical licence state, self-hosted posture, 402.

    Without this the previous test would pass just as well against a gate that had stopped
    gating anything at all."""
    t = await make_tenant("selfhost-unlimited")
    await _set_plan("selfhost-unlimited", "unlimited")
    await _reset_instance_license(grace_started_days_ago=999)
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        r = await c.post(
            "/api/v1/leave/types",
            json={"key": "vacation", "label_i18n": {"nl": "Vakantie", "en": "Vacation"}},
            headers=headers,
        )
        assert r.status_code == 402, r.text
        assert r.json()["error"]["message"] == "errors.license_expired"


async def test_lapsed_trial_is_read_only_and_says_plan_not_licence(
    client_for, license_key, cloud_mode
) -> None:
    """A plan really can run out — and when it does the refusal must name the plan.

    Telling a cloud tenant "de licentie is verlopen" points at a document they do not hold and
    a screen they cannot open; what ran out is their subscription."""
    t = await make_tenant("cloud-lapsed")
    await _set_plan(
        "cloud-lapsed", "trial", trial_ends_at=datetime.now(UTC) - timedelta(days=1)
    )
    await _reset_instance_license(grace_started_days_ago=999)
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        r = await c.post(
            "/api/v1/leave/types",
            json={"key": "vacation", "label_i18n": {"nl": "Vakantie", "en": "Vacation"}},
            headers=headers,
        )
        assert r.status_code == 402, r.text
        assert r.json()["error"]["code"] == "plan_inactive"
        assert r.json()["error"]["message"] == "errors.plan_inactive"
        # Read-only, never gone (epic #140) — data is not hostage to a billing state either.
        assert (await c.get("/api/v1/leave/types", headers=headers)).status_code == 200


async def test_a_live_plan_does_not_unlock_the_cloud_sku_itself(
    client_for, license_key, cloud_mode
) -> None:
    """The one sku the instance key keeps governing on cloud.

    ``cloud`` is the operator's own right to run the posture, so a tenant on ``unlimited``
    must not be able to buy their way into the provisioning surface by holding a plan."""
    assert refusal_for("cloud") == ("license_expired", "errors.license_expired")
    assert refusal_for("leave") == ("plan_inactive", "errors.plan_inactive")


# --------------------------------------------------------------------------- #
# What the screens read
# --------------------------------------------------------------------------- #
async def test_meta_reports_the_plan_not_the_licence(
    client_for, license_key, cloud_mode
) -> None:
    """``entitled_modules`` draws every locked control, so it has to answer from the same
    authority the write gate will — or a button is drawn live and then 402s."""
    t = await make_tenant("cloud-meta")
    await _set_plan("cloud-meta", "unlimited")
    await _reset_instance_license(grace_started_days_ago=999)
    async with client_for(t.host) as c:
        tenant = (await c.get("/api/v1/meta/tenant")).json()
        assert "leave" in tenant["entitled_modules"]
        assert tenant["deployment"] == "cloud"
        # /meta/modules resolves the same org and must not disagree with /meta/tenant.
        assert "leave" in (await c.get("/api/v1/meta/modules")).json()["entitled_modules"]


async def test_meta_locks_a_lapsed_plan(client_for, license_key, cloud_mode) -> None:
    t = await make_tenant("cloud-meta-lapsed")
    await _set_plan(
        "cloud-meta-lapsed", "trial", trial_ends_at=datetime.now(UTC) - timedelta(days=1)
    )
    await _reset_instance_license(grace_started_days_ago=999)
    async with client_for(t.host) as c:
        tenant = (await c.get("/api/v1/meta/tenant")).json()
        assert "leave" in tenant["licensed_modules"]
        assert "leave" not in tenant["entitled_modules"]


# --------------------------------------------------------------------------- #
# notice(): "expired" is a claim about a document that existed
# --------------------------------------------------------------------------- #
async def test_never_licensed_does_not_report_an_expiry(license_key) -> None:
    """A box that never had a key answers ``unlicensed`` inside the bootstrap window and
    ``none`` outside it — never ``expired``, which would describe a licence nobody installed
    and send the reader off to renew something that does not exist."""
    from app.core.entitlements.service import license_state

    await _reset_instance_license()
    assert (await license_state()).notice("leave") == "unlicensed"

    await _reset_instance_license(grace_started_days_ago=999)
    assert (await license_state()).notice("leave") == "none"


# --------------------------------------------------------------------------- #
# Background work
# --------------------------------------------------------------------------- #
async def test_crons_do_not_stand_down_instance_wide_on_cloud(
    license_key, cloud_mode
) -> None:
    """A module's cron guard runs before the per-org fan-out, so on cloud it cannot answer
    per tenant — and answering "no" instance-wide would stop every tenant's background work
    because the *operator* had not installed a key. ``run_per_org`` filters instead."""
    await _reset_instance_license(grace_started_days_ago=999)
    assert await sku_cron_enabled("leave") is True
    # The operator's own sku still answers from the licence, on both postures.
    assert await sku_cron_enabled("cloud") is False


async def test_crons_still_stand_down_on_self_host(license_key) -> None:
    await _reset_instance_license(grace_started_days_ago=999)
    assert await sku_cron_enabled("leave") is False


async def test_run_per_org_skips_a_lapsed_plan_on_cloud(cloud_mode) -> None:
    """The other half of the cron rule, and the branch that has to actually execute.

    ``run_per_org`` is where a cloud tenant's plan is enforced for background work, and it is
    reached only when ``settings.is_cloud`` — so it runs on no ordinary test and is exactly
    where a typo survives a green suite. (It did: this filter shipped its first draft with
    ``OrgPlan`` never imported, which every test passed straight over.)
    """
    from app.core.jobs import run_per_org

    live = await make_tenant("cron-live")
    lapsed = await make_tenant("cron-lapsed")
    await _set_plan("cron-live", "unlimited")
    await _set_plan(
        "cron-lapsed", "trial", trial_ends_at=datetime.now(UTC) - timedelta(days=1)
    )

    seen: list[str] = []

    async def collect(org, session) -> None:
        seen.append(org.slug)

    await run_per_org(collect)
    assert live.org.slug in seen
    assert lapsed.org.slug not in seen
