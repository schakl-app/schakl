"""Leave entitlements re-derive when the contract they were prorated from changes (#264).

Pre-#264, ``seed_entitlements`` only ever *created missing* rows, so an already-seeded year stayed
frozen: terminating an open-ended contract mid-year (an employee who leaves early) left the
full-year balance in place, and a raise via the supported "terminate old + add new" workflow was
ignored for the rest of the year. Now a contract create/correct/terminate recomputes that user's
``generated`` pots for the current and every future year it touches — a shorter span prorates down,
a raise folds both periods in, and a year the contract no longer covers loses its pot — while
``manual`` overrides and closed (past) years stay untouched.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from app.core.auth.models import User
from tests.conftest import auth_cookie, make_tenant

_YEAR = date.today().year


async def _member(client, headers, email: str) -> User:
    res = await client.post(
        "/api/v1/members/invite",
        json={"email": email, "full_name": "Member", "role": "member"},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return User(
        id=uuid.UUID(res.json()["user_id"]), email=email, hashed_password="", is_active=True
    )


async def _types(client, headers) -> dict[str, str]:
    rows = (await client.get("/api/v1/leave/types", headers=headers)).json()
    return {t["key"]: t["id"] for t in rows}


async def _add_contract(client, headers, user_id, **overrides) -> dict:
    body = {
        "user_id": str(user_id),
        "start_date": f"{_YEAR}-01-01",
        "end_date": None,
        "contract_hours_per_week": "40",
        **overrides,
    }
    res = await client.post("/api/v1/leave/contracts", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


async def _statutory(client, headers, user_id, type_id, year=_YEAR) -> float:
    rows = (
        await client.get(
            "/api/v1/leave/entitlements",
            params={"year": year, "user_id": str(user_id)},
            headers=headers,
        )
    ).json()
    match = next((e for e in rows if e["leave_type_id"] == type_id), None)
    return float(match["hours"]) if match else 0.0


def _prorated_statutory(
    segments: list[tuple[date, date | None, float]], year: int = _YEAR
) -> float:
    """Mirror ``seed_entitlements``: ``Σ 4 weeks × contract_hours × overlap_days / year_days``,
    summed then rounded once to the cent — the same order the service uses."""
    jan, dec = date(year, 1, 1), date(year, 12, 31)
    year_days = (dec - jan).days + 1
    total = Decimal(0)
    for start, end, hours in segments:
        s = max(start, jan)
        e = min(end or dec, dec)
        days = (e - s).days + 1 if e >= s else 0
        total += Decimal(4) * Decimal(str(hours)) * Decimal(days) / Decimal(year_days)
    return float(total.quantize(Decimal("0.01")))


async def test_terminating_open_contract_reprorates_down(client_for) -> None:
    """An employee on an open-ended 40 h contract who leaves 30 June keeps only half a year's
    vacation — not the full year the open contract first granted."""
    t = await make_tenant("recompute-terminate")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "e@example.com")
        types = await _types(c, headers)
        contract = await _add_contract(c, headers, member.id)  # open-ended, 40 h

        # Full year to start: 4 × 40.
        assert await _statutory(c, headers, member.id, types["vacation_statutory"]) == 160.0

        res = await c.patch(
            f"/api/v1/leave/contracts/{contract['id']}",
            json={"end_date": f"{_YEAR}-06-30"},
            headers=headers,
        )
        assert res.status_code == 200, res.text

        expected = _prorated_statutory([(date(_YEAR, 1, 1), date(_YEAR, 6, 30), 40)])
        got = await _statutory(c, headers, member.id, types["vacation_statutory"])
        assert got == expected
        assert got < 160.0  # it actually dropped


async def test_raise_via_terminate_and_readd_folds_both_periods(client_for) -> None:
    """The supported raise workflow (#264's headline case): terminate the current contract and add
    a higher one. The year's statutory becomes the SUM of both periods' prorations, not either
    contract's whole-year figure."""
    t = await make_tenant("recompute-raise")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "e@example.com")
        types = await _types(c, headers)
        old = await _add_contract(c, headers, member.id, contract_hours_per_week="36")
        assert await _statutory(c, headers, member.id, types["vacation_statutory"]) == 144.0

        # Terminate the old period end of June...
        res = await c.patch(
            f"/api/v1/leave/contracts/{old['id']}",
            json={"end_date": f"{_YEAR}-06-30"},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        # ...and start the raise on 1 July.
        await _add_contract(
            c, headers, member.id,
            start_date=f"{_YEAR}-07-01", end_date=None, contract_hours_per_week="38",
        )

        expected = _prorated_statutory(
            [
                (date(_YEAR, 1, 1), date(_YEAR, 6, 30), 36),
                (date(_YEAR, 7, 1), None, 38),
            ]
        )
        got = await _statutory(c, headers, member.id, types["vacation_statutory"])
        assert got == expected
        # A blend of the two rates, between the two whole-year figures (144 and 152).
        assert 144.0 < got < 152.0


async def test_in_place_hours_correction_reprorates(client_for) -> None:
    """Even a direct edit of ``contract_hours_per_week`` (a typo fix) now re-derives the year."""
    t = await make_tenant("recompute-inplace")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "e@example.com")
        types = await _types(c, headers)
        contract = await _add_contract(
            c, headers, member.id, end_date=f"{_YEAR}-12-31", contract_hours_per_week="36"
        )
        assert await _statutory(c, headers, member.id, types["vacation_statutory"]) == 144.0

        res = await c.patch(
            f"/api/v1/leave/contracts/{contract['id']}",
            json={"contract_hours_per_week": "40"},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        assert await _statutory(c, headers, member.id, types["vacation_statutory"]) == 160.0


async def test_note_only_edit_does_not_churn_entitlements(client_for) -> None:
    """A note-only correction changes no balance, so it must not delete + recreate the pots."""
    t = await make_tenant("recompute-noteonly")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "e@example.com")
        types = await _types(c, headers)
        contract = await _add_contract(c, headers, member.id, end_date=f"{_YEAR}-12-31")

        before = (
            await c.get(
                "/api/v1/leave/entitlements",
                params={"year": _YEAR, "user_id": str(member.id)},
                headers=headers,
            )
        ).json()
        stat_id_before = next(
            e["id"] for e in before if e["leave_type_id"] == types["vacation_statutory"]
        )

        res = await c.patch(
            f"/api/v1/leave/contracts/{contract['id']}",
            json={"note": "corrected the reference number"},
            headers=headers,
        )
        assert res.status_code == 200, res.text

        after = (
            await c.get(
                "/api/v1/leave/entitlements",
                params={"year": _YEAR, "user_id": str(member.id)},
                headers=headers,
            )
        ).json()
        stat_id_after = next(
            e["id"] for e in after if e["leave_type_id"] == types["vacation_statutory"]
        )
        # Same row id → the pot was left in place, not deleted and regenerated.
        assert stat_id_after == stat_id_before


async def test_terminating_removes_a_future_year_pot(client_for) -> None:
    """A future year the ended contract no longer covers loses its generated pot entirely — a
    departed employee stops accruing for next year, not just the year they left."""
    t = await make_tenant("recompute-futureyear")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "e@example.com")
        types = await _types(c, headers)
        contract = await _add_contract(c, headers, member.id)  # open-ended, 40 h

        # Generate next year org-wide → the open contract earns the member a full next-year pot.
        res = await c.post(
            "/api/v1/leave/entitlements/generate", json={"year": _YEAR + 1}, headers=headers
        )
        assert res.status_code == 200
        assert (
            await _statutory(c, headers, member.id, types["vacation_statutory"], year=_YEAR + 1)
            == 160.0
        )

        # The employee leaves mid-this-year: next year is no longer covered.
        res = await c.patch(
            f"/api/v1/leave/contracts/{contract['id']}",
            json={"end_date": f"{_YEAR}-06-30"},
            headers=headers,
        )
        assert res.status_code == 200, res.text

        # This year reprorated down; next year's pot is gone.
        assert await _statutory(
            c, headers, member.id, types["vacation_statutory"]
        ) == _prorated_statutory([(date(_YEAR, 1, 1), date(_YEAR, 6, 30), 40)])
        next_year_rows = (
            await c.get(
                "/api/v1/leave/entitlements",
                params={"year": _YEAR + 1, "user_id": str(member.id)},
                headers=headers,
            )
        ).json()
        assert next_year_rows == []

        # ...and it *stays* gone even after a balance read triggers on-demand seeding (#108): a
        # departed employee who still holds ``time.entry.write`` must not be handed a fresh
        # next-year pot by the contract-less fallback (that fallback is only for never-contracted
        # staff). Reading the balance would resurrect the row before #264's fallback gate.
        mh = await auth_cookie(
            User(id=member.id, email="e@example.com", hashed_password="", is_active=True)
        )
        res = await c.get("/api/v1/leave/balance", params={"year": _YEAR + 1}, headers=mh)
        assert res.status_code == 200
        assert (
            await c.get(
                "/api/v1/leave/entitlements",
                params={"year": _YEAR + 1, "user_id": str(member.id)},
                headers=headers,
            )
        ).json() == []


async def test_deleting_a_contract_re_derives(client_for) -> None:
    """Hard-deleting the only contract strips the pots it justified (they were prorated from a
    period that no longer exists)."""
    t = await make_tenant("recompute-delete")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "e@example.com")
        types = await _types(c, headers)
        contract = await _add_contract(c, headers, member.id, end_date=f"{_YEAR}-12-31")
        assert await _statutory(c, headers, member.id, types["vacation_statutory"]) == 160.0

        res = await c.delete(f"/api/v1/leave/contracts/{contract['id']}", headers=headers)
        assert res.status_code in (200, 204), res.text

        # No contract left covering the year → the generated pot is removed (the member is not a
        # legacy ``time.entry.write`` holder here, so nothing falls back to a full pot).
        rows = (
            await c.get(
                "/api/v1/leave/entitlements",
                params={"year": _YEAR, "user_id": str(member.id)},
                headers=headers,
            )
        ).json()
        assert rows == []
