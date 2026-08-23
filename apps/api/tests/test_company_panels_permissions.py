"""Deny-by-default for the company hub's composed panels (issue #365).

``GET /companies/{id}/panels`` declares ``companies.company.read`` once and then composes every
enabled module's panel. Whether each provider checked the caller's own permission was left to it
to remember, and seven of thirteen did not — so a member holding exactly that one key received
the client's contacts, projects, tasks, hours (description, minutes and whether we bill for it),
websites, domains with their resolved prices, and the full change history with actor names.

The rule this file enforces is the one already written down for ``EntityPanelSpec`` on the web
registry and never applied to the API's ``PanelSpec``: a contributed panel declares the
permission its data sits behind, or says in words why it needs none.

Three tests, in the order they must be trusted — the shape ``test_rbac_deny_by_default`` uses:

1. **Declaration.** Every registered panel carries ``requires_permission`` or an
   ``explicit_public`` reason, and the permission it names exists in the catalog. A new module
   contributing a panel without one is a build break.
2. **Filtering.** ``panels_for`` never returns a panel the viewer may not read — the provider is
   not called, which is the whole point: a check that still runs the query saves no round trip
   and produced the answer anyway.
3. **Behaviour.** The endpoint, called by a member holding nothing but ``companies.company.read``,
   answers with no panel that declares a permission. Introspection can go vacuous; a payload
   carrying somebody's hours cannot.
"""

from __future__ import annotations

from sqlalchemy import text

from app.core.permissions.catalog import all_permissions
from app.db import async_session_maker, set_current_org
from app.registry import PROMINENCE_PRIMARY, PROMINENCE_REGISTER, SIZE_FULL, SIZE_HALF, registry
from tests.conftest import add_membership, auth_cookie, make_tenant


def _all_company_panels():
    return registry.panels_for("company", [m.name for m in registry.all()])


def test_every_company_panel_declares_a_permission_or_says_why_not() -> None:
    known = {spec.key for spec in all_permissions()}
    undeclared: list[str] = []
    for spec in _all_company_panels():
        if spec.requires_permission is None:
            if not spec.explicit_public:
                undeclared.append(spec.key)
            continue
        assert spec.requires_permission in known, (
            f"{spec.key} declares unknown permission {spec.requires_permission!r}"
        )
        assert spec.requires_scope in (None, "own", "any"), (
            f"{spec.key}: bad scope {spec.requires_scope!r}"
        )
    assert not undeclared, (
        "these panels declare neither requires_permission nor explicit_public, so the company "
        "hub composes them for anyone who can open the client (#365):\n  "
        + "\n  ".join(undeclared)
    )


def test_every_company_panel_declares_a_layout() -> None:
    """``prominence`` and ``size`` are a closed vocabulary (#364), not free text.

    A typo would silently land the panel in the register lane, which is the failure this page
    was redesigned to stop making by accident.
    """
    for spec in _all_company_panels():
        assert spec.prominence in (PROMINENCE_PRIMARY, PROMINENCE_REGISTER), (
            f"{spec.key}: unknown prominence {spec.prominence!r}"
        )
        assert spec.size in (SIZE_FULL, SIZE_HALF), f"{spec.key}: unknown size {spec.size!r}"


def test_the_client_s_own_details_are_a_working_surface() -> None:
    """#403: the one panel #364 sorted by what it is called rather than what it is used for.

    Filed as a register it sat under every working surface on the page — roughly 1.100 px down
    on a well-filled client, below a block of logged hours. A register is something you
    occasionally consult; a client's telephone number is what somebody opens the page for when
    the phone rings.
    """
    details = next(spec for spec in _all_company_panels() if spec.key == "companies.details")
    assert details.prominence == PROMINENCE_PRIMARY
    assert details.size == SIZE_HALF


def test_every_company_summary_declares_a_permission() -> None:
    """The vital-signs strip is the panels seam one level up, so it carries the same rule."""
    known = {spec.key for spec in all_permissions()}
    for spec in registry.summaries_for("company", [m.name for m in registry.all()]):
        assert spec.requires_permission or spec.explicit_public, (
            f"{spec.key} declares neither requires_permission nor explicit_public (#365)"
        )
        if spec.requires_permission:
            assert spec.requires_permission in known, (
                f"{spec.key} declares unknown permission {spec.requires_permission!r}"
            )


def test_panels_for_drops_what_the_viewer_may_not_read() -> None:
    """The filter is in the registry, so the provider is never *called*."""
    names = [m.name for m in registry.all()]
    holds_nothing = registry.panels_for("company", names, lambda key, scope=None: False)
    assert {spec.key for spec in holds_nothing} == {
        spec.key for spec in _all_company_panels() if spec.requires_permission is None
    }, "a panel declaring a permission survived a viewer who holds none of them"

    only_tasks = registry.panels_for(
        "company", names, lambda key, scope=None: key == "tasks.task.read"
    )
    assert "tasks.company" in {spec.key for spec in only_tasks}
    assert "time.company" not in {spec.key for spec in only_tasks}


async def test_the_hub_hands_a_bare_member_no_module_data(client_for) -> None:
    """The real gate: a member holding exactly ``companies.company.read``."""
    owner = await make_tenant("panels-perm")
    member = await make_tenant("panels-perm-m", email="panelmember@example.com")
    async with async_session_maker() as session:
        await set_current_org(session, owner.org.id)
        await add_membership(session, owner.org.id, member.user.id, role="member")
        await session.commit()

    owner_headers = await auth_cookie(owner.user)
    member_headers = await auth_cookie(member.user, org_id=owner.org.id)
    async with client_for(owner.host) as client:
        company = (
            await client.post(
                "/api/v1/companies", json={"name": "Panelklant"}, headers=owner_headers
            )
        ).json()
        assert "id" in company, company

        # Strip the `member` role to a single permission — the exact posture the issue was
        # verified against. Done *after* the client exists, so the owner could still make one.
        async with async_session_maker() as session:
            await set_current_org(session, owner.org.id)
            await session.execute(
                text(
                    "DELETE FROM role_permissions WHERE org_id = :org AND permission <> "
                    "'companies.company.read' AND role_id IN "
                    "(SELECT id FROM roles WHERE org_id = :org AND key = 'member')"
                ),
                {"org": str(owner.org.id)},
            )
            await session.commit()

        response = await client.get(
            f"/api/v1/companies/{company['id']}/panels", headers=member_headers
        )
        assert response.status_code == 200, response.text
        returned = {panel["key"] for panel in response.json()}

    gated = {spec.key for spec in _all_company_panels() if spec.requires_permission}
    leaked = returned & (gated - {"companies.details"})
    assert not leaked, (
        "the hub composed these panels for a member holding only companies.company.read:\n  "
        + "\n  ".join(sorted(leaked))
    )
    # Anti-vacuum: the caller *does* still get the record's own definition, so an empty payload
    # cannot be mistaken for a passing test.
    assert "companies.details" in returned
