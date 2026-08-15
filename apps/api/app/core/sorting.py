"""Server-side sorting for list endpoints (CLAUDE.md §9, docs/PERFORMANCE.md).

Sorting stays on the server: a list is paginated, so sorting the page you happen to have in the
browser sorts the wrong set. Callers pass ``?sort=name`` or ``?sort=-updated_at`` (a leading ``-``
means descending) and each endpoint supplies an **allow-list** mapping those keys to columns.

The allow-list is not decoration. ``sort`` arrives from the URL, so an unknown key is rejected
outright rather than reaching anywhere near a query — no attacker-chosen column names, no ordering
by a column the response never exposes.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, func

from app.errors import AppError


def user_sort_name(user_id_column: Any) -> Any:
    """Order by a person's **display name**, the way the UI names them.

    A list sorted by an employee column must not order by their user id, and `full_name` is
    nullable — the UI falls back to the email, so the sort has to as well, or the list stops
    ordering the way it reads. Correlated: it joins nothing, so a row is never multiplied.

    Takes the FK column, so the same rule serves an assignee, an approver and a leave requester.
    """
    from sqlalchemy import select

    from app.core.auth.models import User

    return (
        select(func.lower(func.coalesce(User.full_name, User.email)))
        .where(User.id == user_id_column)
        .scalar_subquery()
    )


def parse_sort(sort: str | None, allowed: dict[str, Any]) -> tuple[str, bool] | None:
    """``"-updated_at"`` → ``("updated_at", True)``. ``None``/empty → ``None`` (use the default)."""
    if not sort:
        return None
    descending = sort.startswith("-")
    key = sort[1:] if descending else sort
    if key not in allowed:
        raise AppError(
            "invalid_sort",
            "errors.invalid_sort",
            status_code=400,
            fields={"sort": "errors.invalid_sort"},
        )
    return key, descending


def apply_sort(
    stmt: Select,
    sort: str | None,
    allowed: dict[str, Any],
    *,
    default: Any,
    tiebreak: Any | None = None,
) -> Select:
    """Order ``stmt`` by the requested column, else by ``default``.

    ``NULLS LAST`` on every sort: a row with no due date or no budget belongs at the bottom in
    both directions, not floating to the top of a descending list.

    **A sort on a non-unique column always carries a tiebreaker** (#360). Without one the rows
    inside a group come back in whatever order the plan produced — ``/leave/team`` sorted by
    Medewerker listed one employee's twelve requests as *13 nov, 27 nov, 11 dec, 30 okt, 24 jul,
    …*, which is worse than an obviously wrong order because it is *nearly* right and so nobody
    checks it. The tiebreaker defaults to the list's own default ordering, so every caller gets
    one without asking; a list whose grouped reading order differs from its default (leave reads
    next absence first and defaults to most recent first) names its own.
    """
    parsed = parse_sort(sort, allowed)
    if parsed is None:
        return stmt.order_by(default)
    key, descending = parsed
    column = allowed[key]
    ordering = column.desc().nulls_last() if descending else column.asc().nulls_last()
    return stmt.order_by(ordering, tiebreak if tiebreak is not None else default)
