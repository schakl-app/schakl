"""``?due=`` is a partition, and the tile's rows are ordered by urgency (#397).

The dashboard's personal task tile filed *this afternoon's week*, *next month* and *every task
with no deadline* under one heading called **Binnenkort**, because it partitioned into three
buckets and the third was "everything that is not overdue and not today". The team asked for
today to be the tile's subject with the week and the rest separated from it, which needs four.

Four sections whose headings link into the list only work if the list agrees about where the
boundaries are, so the four ``?due=`` values *are* the four buckets — same names, same edges,
declared once on the web in ``$lib/modules/tasks/due.ts`` and pinned on both sides. Two things
had to change here for that to be true:

* ``week`` started **at** today, so it was a superset of ``today`` — harmless as a lone filter
  chip, and wrong the moment a section headed "Deze week (2)" opens a list of three.
* there was no ``later`` at all, so the far end of the list and every undated row had no
  destination. An undated task is ``later``: it has to be somewhere, or the four values do not
  cover the list and the tile silently drops rows — which is exactly what the old third bucket
  did by sweeping them up beside next Tuesday's work.

And the tile's own order gained the priority tiebreak the team asked for: the rows used to fall
back on ``created_at``, i.e. the order they happened to be typed in.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import text

from app.db import async_session_maker, set_current_org
from tests.conftest import auth_cookie, default_company, make_tenant, org_today


async def _task(c, headers, title: str, due, **extra) -> dict:
    res = await c.post(
        "/api/v1/tasks",
        json={
            "company_id": await default_company(c, headers),
            "title": title, "due_date": due.isoformat(), **extra,
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()


async def _titles(c, headers, due: str) -> list[str]:
    res = await c.get("/api/v1/tasks", params={"due": due}, headers=headers)
    assert res.status_code == 200, res.text
    return [row["title"] for row in res.json()["items"]]


async def _undate(org_id, task_id: str) -> None:
    """The one shape no API can produce any more (#392) and every upgraded instance carries."""
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        await session.execute(
            text("UPDATE tasks SET due_date = NULL WHERE id = :id"), {"id": uuid.UUID(task_id)}
        )
        await session.commit()


async def test_the_four_due_values_partition_the_list(client_for) -> None:
    t = await make_tenant("due-buckets")
    headers = await auth_cookie(t.user)
    today = org_today()
    async with client_for(t.host) as c:
        await _task(c, headers, "Gisteren", today - timedelta(days=1))
        await _task(c, headers, "Vandaag", today)
        await _task(c, headers, "Morgen", today + timedelta(days=1))
        await _task(c, headers, "Zevende dag", today + timedelta(days=7))
        await _task(c, headers, "Achtste dag", today + timedelta(days=8))
        undated = await _task(c, headers, "Zonder datum", today + timedelta(days=2))
        await _undate(t.org.id, undated["id"])

        assert await _titles(c, headers, "overdue") == ["Gisteren"]
        assert await _titles(c, headers, "today") == ["Vandaag"]
        # Today is *not* in "deze week": the two are sections of one tile, and a section that
        # contains another one cannot be drawn.
        assert sorted(await _titles(c, headers, "week")) == ["Morgen", "Zevende dag"]
        assert sorted(await _titles(c, headers, "later")) == ["Achtste dag", "Zonder datum"]

        # Together they are the whole list — no task belongs to two and none belongs to none.
        buckets = ["overdue", "today", "week", "later"]
        seen = [title for bucket in buckets for title in await _titles(c, headers, bucket)]
        every = await c.get("/api/v1/tasks", headers=headers)
        assert sorted(seen) == sorted(row["title"] for row in every.json()["items"])


async def test_the_tile_breaks_a_date_tie_on_priority(client_for) -> None:
    """Date first, then priority — the order the board sorts by, so the two agree."""
    t = await make_tenant("due-tile-order")
    headers = await auth_cookie(t.user)
    today = org_today()
    mine = {"assignee_user_id": str(t.user.id)}
    async with client_for(t.host) as c:
        # Created low-first, so a tile still ordered on `created_at` would answer in this order.
        await _task(c, headers, "Rustig", today + timedelta(days=1), priority="low", **mine)
        await _task(c, headers, "Gewoon", today + timedelta(days=1), priority="normal", **mine)
        await _task(c, headers, "Brand", today + timedelta(days=1), priority="high", **mine)
        # A nearer deadline still outranks a louder priority: the date is the first key.
        await _task(c, headers, "Vandaag rustig", today, priority="low", **mine)

        res = await c.get("/api/v1/tasks/dashboard-mine", headers=headers)
        assert res.status_code == 200
        assert [row["title"] for row in res.json()["items"]] == [
            "Vandaag rustig",
            "Brand",
            "Gewoon",
            "Rustig",
        ]
        # And the counts beside the headings are the whole set's, not the page's (#407).
        assert res.json()["due_today"] == 1
        assert res.json()["due_week"] == 3
        assert res.json()["later"] == 0
