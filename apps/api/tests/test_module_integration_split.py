"""Modules vs integrations (CLAUDE.md §6a).

Three things are asserted here, and each one is a rule the code would otherwise only *look*
like it follows:

1. **The tree matches the declaration.** A descriptor in ``app/integrations/`` says
   ``kind="integration"`` and one in ``app/modules/`` does not. The split's whole value is that
   a path predicts a kind; the moment they disagree, both are noise.
2. **A requirement is met in both directions.** Enabling an integration without the module it
   attaches to is refused, and so is switching that module off afterwards — which is the case a
   delta check silently allows and the one that actually happens.
3. **A requirement names something real.** A typo in ``requires`` is a rule that never fires.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

from app.config import settings
from app.core.entitlements.service import ensure_requirements_met
from app.errors import AppError
from app.registry import KIND_INTEGRATION, KIND_MODULE, MODULE_ROOTS, module_package, registry
from tests.conftest import make_tenant


def _packages(root: str) -> list[str]:
    package = importlib.import_module(root)
    return sorted(
        m.name for m in pkgutil.iter_modules(package.__path__) if not m.name.startswith("_")
    )


def test_every_enabled_module_resolves_to_exactly_one_root() -> None:
    """``module_package`` is what every dynamic load asks; an unresolvable name boots an
    instance with a module silently absent from the registry, 404ing every route it owns."""
    for name in settings.enabled_modules:
        assert module_package(name) is not None, f"'{name}' is in neither of {MODULE_ROOTS}"


def test_every_enabled_module_actually_registers() -> None:
    """Resolving to a path is not the same fact as being *there* (#381).

    A directory with no ``__init__.py`` is a PEP 420 namespace package: ``find_spec`` finds it,
    ``import_module`` succeeds, and nothing registers. #378 moved four integrations out of
    ``app/modules/``, git does not track empty directories, and every developer checkout that
    predated the move kept ``app/modules/<name>/__pycache__/`` husks — which shadowed the real
    packages, because ``app.modules`` is searched first. The app booted with 23 modules instead
    of 27 and said nothing; the only visible symptom was fifty-one endpoints missing from a
    regenerated client.

    The test above passed throughout, because a path *was* returned. This is the invariant that
    was actually broken, so this is the one worth asserting.
    """
    import app.main  # noqa: F401 — importing the app is what loads the enabled modules

    missing = [name for name in settings.enabled_modules if registry.get(name) is None]
    assert not missing, (
        f"enabled but not registered: {missing}. A leftover directory under one of "
        f"{MODULE_ROOTS} shadows the real package — remove it, or the module is simply absent."
    )


def test_the_package_a_descriptor_lives_in_matches_the_kind_it_declares() -> None:
    for root, expected in (("app.modules", KIND_MODULE), ("app.integrations", KIND_INTEGRATION)):
        for name in _packages(root):
            if name not in settings.enabled_modules:
                continue
            importlib.import_module(f"{root}.{name}")
            descriptor = registry.get(name)
            assert descriptor is not None, f"{root}.{name} registered no descriptor"
            assert descriptor.kind == expected, (
                f"'{name}' lives in {root} but declares kind={descriptor.kind!r}. "
                "A path that does not predict a kind makes both of them noise (CLAUDE.md §6a)."
            )


def test_every_declared_requirement_names_a_registered_module() -> None:
    known = set(registry.kinds())
    for name, needs in registry.requirements().items():
        for need in needs:
            assert need in known, f"'{name}' requires '{need}', which no module declares"
            assert need != name, f"'{name}' requires itself"


def test_a_requirement_is_never_a_module_that_requires_the_declarer() -> None:
    """No cycles. The settings screen resolves requirements by fixpoint and the API by one
    pass; a cycle would make the first loop forever and the second unsatisfiable."""
    requires = registry.requirements()
    for name in requires:
        seen: set[str] = set()
        stack = list(requires.get(name, []))
        while stack:
            need = stack.pop()
            assert need != name, f"requirement cycle through '{name}'"
            if need in seen:
                continue
            seen.add(need)
            stack.extend(requires.get(need, []))


def test_enabling_an_integration_without_its_module_is_refused() -> None:
    requires = registry.requirements()
    if not requires:
        pytest.skip("no integration declares a requirement in this build")
    name, needs = next(iter(requires.items()))
    with pytest.raises(AppError) as excinfo:
        ensure_requirements_met(["companies", name])
    assert excinfo.value.code == "module_requirements_unmet"
    assert excinfo.value.status_code == 409
    # And the same set *with* the requirement is fine.
    ensure_requirements_met(["companies", name, *needs])


def test_switching_off_a_required_module_is_refused_too() -> None:
    """The delta-blind half. ``requires`` is checked over the resulting set, so removing
    ``domains`` from a workspace already running Cloudflare is the same refusal as adding
    Cloudflare to one that never had ``domains`` — a check on newly-enabled names sees the
    first as a removal and allows it."""
    requires = registry.requirements()
    name, needs = next((k, v) for k, v in requires.items() if v)
    ensure_requirements_met(["companies", name, *needs])
    with pytest.raises(AppError) as excinfo:
        ensure_requirements_met(["companies", name])  # the module was switched off
    assert excinfo.value.code == "module_requirements_unmet"


def test_a_requirement_this_build_does_not_ship_is_not_reported() -> None:
    """An instance may mount a subset. Refusing to enable Cloudflare on a box with no
    ``domains`` package would be a dead end rather than something the reader can fix."""
    assert registry.unmet_requirements(["companies"]) == {}


async def test_meta_modules_serves_the_classification(client_for) -> None:
    tenant = await make_tenant("modkind")
    async with client_for(tenant.host) as c:
        response = await c.get("/api/v1/meta/modules")
    assert response.status_code == 200
    body = response.json()
    kinds = body["module_kinds"]
    assert kinds, "no classification served"
    assert set(kinds.values()) <= {KIND_MODULE, KIND_INTEGRATION}
    # The settings screen reads both from this one payload, so each requirement must be
    # classifiable from it — otherwise a row renders under no heading at all.
    for name, needs in body["module_requires"].items():
        assert name in kinds
        assert all(need in kinds for need in needs)
