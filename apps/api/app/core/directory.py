"""Cross-module entity **references** — the lookup seam (issue #285's rule, generalised).

A module routinely has to name another module's rows without owning them. An interaction
labels each participant address with the contact it matches (#160) and validates the contacts
someone @mentioned in a note (#165); a task comment does the same (#165). None of them may
import the contacts module's internals (CLAUDE.md §6), so each grew the same bare-table read:

    SELECT id FROM contacts WHERE org_id = :oid AND …

Right about the tenant, and blind to the **company horizon**. That is not an oversight anyone
could have avoided by being careful: a contact carries no ``company_id`` at all — it belongs to
whatever ``company_contacts`` links it to — so the horizon lives in a join the borrowing module
is not allowed to know about (#285's first failure mode, "no anchor"). The visible consequence
was that a member scoped to one company group read every *other* client's contact chips off an
e-mail thread they could legitimately see, and could mention any contact in the org by id.

The obvious fix — teach interactions the ``company_contacts`` join — is the one §15 warns
against: the horizon shape copied into every borrower, and four places to forget it next time.
So the crossing gets a seam instead, and it is deliberately thin, because everything it needs
already exists:

* **which model** an ``entity_type`` names comes from the registry ``AuditableMixin`` /
  ``CustomizableMixin`` already fill (``core/scope.py``) — core still holds no module list;
* **what the caller may see** comes from that model's own repository
  (``horizon_condition()``), so an indirect link is honoured by the module that declared it;
* **fail closed**: an ``entity_type`` no enabled module registered resolves to nothing, and an
  unresolvable reference is dropped — which is exactly what the callers already did with an id
  from another tenant.

The reads stay lean on purpose (``docs/PERFORMANCE.md``): both are one batched, column-only
statement over a page's distinct values, never a row-by-row check and never a loaded entity.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from app.core.scope import PORTAL_CLAUSE_ATTR, horizon_entity_model

if TYPE_CHECKING:  # only for the annotation — importing tenancy here would cycle.
    from app.core.tenancy import RequestContext

#: A model naming the column that holds its e-mail address opts into :func:`ids_by_email`.
#: Declared on the model beside ``__entity_type__``, for the same reason: the rule about a
#: model belongs to the model, and core never carries a per-module table.
EMAIL_ATTR = "__directory_email__"

#: A model whose display column is not called ``name`` says so here, for :func:`labels_for`.
LABEL_ATTR = "__directory_label__"

# ``PORTAL_CLAUSE_ATTR`` — a model whose **external (client) login** rule is stricter than its
# staff horizon — moved to ``app/core/scope.py`` in #266, because ``entity_visible`` there needs
# the same fact and this module imports that one. Both seams read the one definition; that is
# the whole point of moving it rather than declaring it twice.


def _visible_select(ctx: RequestContext, model: type):
    """A column-select over ``model``, filtered to this tenant **and** this caller's horizon.

    A portal caller gets ``PORTAL_CLAUSE_ATTR`` where the model declares one: restricted staff
    still see an unattached contact, a client never does (#193). Handing a client login the
    *staff* rule is precisely the "second copy of the predicate" §15 warns about — in the other
    direction.
    """
    stmt = select(model.id).where(model.org_id == ctx.org.id)
    portal_clause = getattr(model, PORTAL_CLAUSE_ATTR, None) if ctx.is_portal else None
    if portal_clause is not None:
        return stmt.where(portal_clause(ctx.company_scope))
    condition = ctx.repo(model).horizon_condition()
    return stmt if condition is None else stmt.where(condition)


async def visible_ids(
    ctx: RequestContext, entity_type: str, ids: Iterable[uuid.UUID]
) -> set[uuid.UUID]:
    """Which of ``ids`` name rows of ``entity_type`` this caller may actually see.

    The batched form of :func:`app.core.scope.entity_visible`, for a borrowing module holding a
    list of foreign ids — a note's @mentions, a row's stored references. Anything not returned
    should be dropped by the caller exactly as a cross-tenant id always was: a reference the
    caller cannot see must not round-trip back to them as a rendered chip.
    """
    wanted = list(dict.fromkeys(ids))
    if not wanted:
        return set()
    model = horizon_entity_model(entity_type)
    if model is None:
        return set()
    rows = await ctx.session.execute(
        _visible_select(ctx, model).where(model.id.in_(wanted))
    )
    return set(rows.scalars())


async def labels_for(
    ctx: RequestContext, entity_type: str, ids: Iterable[uuid.UUID | None]
) -> dict[uuid.UUID, str]:
    """The display label of each visible row of ``entity_type`` among ``ids``.

    The third question a borrowing module asks about somebody else's rows, after "may I see
    these?" and "which one owns this address?": *what do I call this one on screen?* Every
    module that wanted it grew its own ``SELECT id, name FROM companies`` — which is tenant-safe
    only by RLS and horizon-blind by construction (§15's failure mode 3), and is a second copy
    of a predicate that lives on the model. Here it is one copy, and an id the caller may not
    see is simply absent rather than answered with a name.

    ``LABEL_ATTR`` lets a model name its own display column; ``name`` is the default because it
    is what almost every entity here calls it.
    """
    wanted = [value for value in dict.fromkeys(ids) if value is not None]
    if not wanted:
        return {}
    model = horizon_entity_model(entity_type)
    if model is None:
        return {}
    column = getattr(model, getattr(model, LABEL_ATTR, "name"), None)
    if column is None:
        return {}
    rows = await ctx.session.execute(
        _visible_select(ctx, model).add_columns(column).where(model.id.in_(wanted))
    )
    return {row[0]: row[1] for row in rows if row[1]}


async def ids_by_email(
    ctx: RequestContext, entity_type: str, emails: Iterable[str]
) -> dict[str, uuid.UUID]:
    """Map each of ``emails`` (lower-cased) to the visible ``entity_type`` row that owns it.

    The read-time match behind participant chips (#160): a page's distinct addresses in one
    statement, so a contact created after the e-mail was logged still links up. Display data,
    never authorization — but *which* display data is exactly the horizon's business, so an
    address belonging to a client outside the caller's scope simply does not resolve.

    Silent on an ``entity_type`` whose model declares no address column: a directory lookup it
    never opted into should answer nothing, not guess at a column named ``email``.
    """
    wanted = {e.lower() for e in emails if e}
    if not wanted:
        return {}
    model = horizon_entity_model(entity_type)
    column_name = getattr(model, EMAIL_ATTR, None) if model is not None else None
    if model is None or column_name is None:
        return {}
    column = getattr(model, column_name)
    rows = await ctx.session.execute(
        _visible_select(ctx, model)
        .add_columns(func.lower(column))
        .where(func.lower(column).in_(wanted))
    )
    return {email: row_id for row_id, email in rows.all()}
