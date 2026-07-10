"""Server-side sorting for the task board (#41).

The board keeps its three status sections and sorts *inside* them, so the interesting cases are
the two columns a naive ``ORDER BY`` gets wrong: ``priority`` and ``status`` are stored as
strings, and alphabetical order is not the order anybody reads them in. ``assignee`` orders by the
employee's display name, never by their user id.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from pwdlib import PasswordHash

from app.core.auth.models import User
from app.db import async_session_maker, set_current_org
from tests.conftest import add_membership, auth_cookie, make_tenant

_ph = PasswordHash.recommended()


async def _member(org_id, email: str, full_name: str | None) -> User:
    async with async_session_maker() as session:
        user = User(
            id=uuid.uuid4(),
            email=email,
            full_name=full_name,
            hashed_password=_ph.hash("secret1234"),
            is_active=True,
            is_verified=True,
        )
        session.add(user)
        await session.flush()
        await set_current_org(session, org_id)
        await add_membership(session, org_id, user.id, "member")
        await session.commit()
        return User(id=user.id, email=user.email, hashed_password="", is_active=True)


async def _task(client, headers, title: str, **fields) -> str:
    body = {"title": title, "status": "open", "priority": "normal", **fields}
    res = await client.post("/api/v1/tasks", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _titles(client, headers, query: str) -> list[str]:
    page = (await client.get(f"/api/v1/tasks?{query}", headers=headers)).json()
    return [item["title"] for item in page["items"]]


async def test_priority_sorts_by_urgency_not_by_spelling(client_for) -> None:
    """Alphabetically it is high < low < normal. Nobody reads a priority that way."""
    t = await make_tenant("tk-prio")
    async with client_for(t.host) as c:
        h = await auth_cookie(t.user)
        await _task(c, h, "normal one", priority="normal")
        await _task(c, h, "high one", priority="high")
        await _task(c, h, "low one", priority="low")

        assert await _titles(c, h, "sort=priority") == ["low one", "normal one", "high one"]
        # Descending is the useful direction: the fires float to the top.
        assert await _titles(c, h, "sort=-priority") == ["high one", "normal one", "low one"]


async def test_status_sorts_along_the_workflow(client_for) -> None:
    t = await make_tenant("tk-status")
    async with client_for(t.host) as c:
        h = await auth_cookie(t.user)
        done = await _task(c, h, "done one")
        await _task(c, h, "open one")
        in_progress = await _task(c, h, "busy one")
        for task_id, status in ((done, "done"), (in_progress, "in_progress")):
            res = await c.patch(f"/api/v1/tasks/{task_id}", json={"status": status}, headers=h)
            assert res.status_code == 200, res.text

        assert await _titles(c, h, "sort=status") == ["open one", "busy one", "done one"]


async def test_assignee_sorts_by_display_name_falling_back_to_the_email(client_for) -> None:
    t = await make_tenant("tk-assignee", email="owner@tk.test")
    zoe = await _member(t.org.id, "zoe@tk.test", "Zoe Zwart")
    ann = await _member(t.org.id, "ann@tk.test", "Ann Appel")
    # No full name: the UI prints the email, so the sort has to file them under "b".
    bob = await _member(t.org.id, "bob@tk.test", None)

    async with client_for(t.host) as c:
        h = await auth_cookie(t.user)
        for name, user in (("zoe task", zoe), ("ann task", ann), ("bob task", bob)):
            await _task(c, h, name, assignee_user_id=str(user.id))

        assert await _titles(c, h, "sort=assignee") == ["ann task", "bob task", "zoe task"]
        assert await _titles(c, h, "sort=-assignee") == ["zoe task", "bob task", "ann task"]


async def test_sorting_by_assignee_never_duplicates_a_task(client_for) -> None:
    """The name subquery is correlated; a join through the users table would multiply the row."""
    t = await make_tenant("tk-dup")
    async with client_for(t.host) as c:
        h = await auth_cookie(t.user)
        await _task(c, h, "only", assignee_user_id=str(t.user.id))
        page = (await c.get("/api/v1/tasks?sort=assignee", headers=h)).json()
        assert len(page["items"]) == 1
        assert page["total"] == 1


async def test_due_date_sorts_with_undated_tasks_last_in_both_directions(client_for) -> None:
    t = await make_tenant("tk-due")
    async with client_for(t.host) as c:
        h = await auth_cookie(t.user)
        today = date.today()
        await _task(c, h, "later", due_date=(today + timedelta(days=5)).isoformat())
        await _task(c, h, "soon", due_date=today.isoformat())
        await _task(c, h, "someday")  # no due date

        assert await _titles(c, h, "sort=due_date") == ["soon", "later", "someday"]
        assert await _titles(c, h, "sort=-due_date") == ["later", "soon", "someday"]


async def test_title_sorts_case_insensitively(client_for) -> None:
    t = await make_tenant("tk-title")
    async with client_for(t.host) as c:
        h = await auth_cookie(t.user)
        await _task(c, h, "banana")
        await _task(c, h, "Apple")
        await _task(c, h, "cherry")
        assert await _titles(c, h, "sort=title") == ["Apple", "banana", "cherry"]


async def test_unsorted_tasks_keep_the_dragged_board_order(client_for) -> None:
    """No ``?sort=`` must not silently become "sorted by something"."""
    t = await make_tenant("tk-position")
    async with client_for(t.host) as c:
        h = await auth_cookie(t.user)
        first = await _task(c, h, "zzz first")
        await _task(c, h, "aaa second")
        # Drag the first card below the second one.
        res = await c.patch(f"/api/v1/tasks/{first}", json={"position": 9999.0}, headers=h)
        assert res.status_code == 200, res.text
        # `position` decides, not the title and not the creation order.
        assert await _titles(c, h, "limit=50") == ["aaa second", "zzz first"]


async def test_unknown_task_sort_key_is_refused(client_for) -> None:
    t = await make_tenant("tk-bad")
    async with client_for(t.host) as c:
        h = await auth_cookie(t.user)
        bad = await c.get("/api/v1/tasks?sort=hashed_password", headers=h)
        assert bad.status_code == 400
        assert bad.json()["error"]["message"] == "errors.invalid_sort"


async def test_task_sort_never_crosses_tenants(client_for) -> None:
    a = await make_tenant("tk-iso-a")
    b = await make_tenant("tk-iso-b")
    async with client_for(a.host) as c:
        await _task(c, await auth_cookie(a.user), "tenant a task")
    async with client_for(b.host) as c:
        h = await auth_cookie(b.user)
        page = (await c.get("/api/v1/tasks?sort=assignee", headers=h)).json()
        assert page["items"] == []
        assert page["total"] == 0
