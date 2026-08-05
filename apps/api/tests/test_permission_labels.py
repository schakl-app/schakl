"""Every catalog permission renders a name, in every locale (CLAUDE.md §8, §15).

The role editor draws its matrix from ``GET /api/v1/permissions/catalog`` and translates each
row with ``t(permission.label_key)`` — and Paraglide's ``t`` falls back to **the key itself**.
So a permission whose label was never added does not fail, anywhere: it ships, and the admin
handing out rights reads ``permissions.time.entry_type.manage`` and has to guess what it grants.
That is the failure this file exists to make loud, because nothing else can see it:

- ``i18n:check`` compares locales against each other, so a key missing from *both* is in sync;
- the deny-by-default sweep (``test_rbac_deny_by_default``) proves a route *declares* a
  permission, never that the permission is nameable;
- and a missing label is invisible in a screenshot unless you happen to open that group.

Three assertions, because there are three ways to end up unreadable: a spec with no label, a
group heading with no label (the accordion summary translates ``permissions.group.<group>`` and
falls back the same way), and a label left behind by a renamed or deleted spec.

A permission is added in Python, so this lives with the Python — and the ``api`` CI job also
runs on ``messages/**`` (``.github/workflows/ci.yml``), so deleting a label trips it too.
"""

from __future__ import annotations

import pytest

from app.config import Settings, settings
from app.core.permissions.catalog import all_permissions
from app.i18n import _catalogs

#: The label a group's accordion summary translates in ``PermissionMatrix.svelte``.
GROUP_KEY = "permissions.group.{group}"


def _locales() -> dict[str, dict[str, str]]:
    catalogs = _catalogs()
    assert catalogs, "no message catalogs loaded — settings.messages_dir is wrong"
    assert "en" in catalogs and "nl" in catalogs, f"expected en+nl, got {sorted(catalogs)}"
    return catalogs


def test_every_permission_has_a_label_in_every_locale() -> None:
    missing: list[str] = []
    for locale, catalog in _locales().items():
        for spec in all_permissions():
            if not catalog.get(spec.i18n_key, "").strip():
                missing.append(f"{locale}.json: {spec.i18n_key}  (permission {spec.key})")
    assert not missing, (
        "permissions with no name — the role editor renders the raw key:\n  "
        + "\n  ".join(sorted(missing))
    )


def test_every_permission_group_has_a_heading_in_every_locale() -> None:
    groups = sorted({spec.module for spec in all_permissions()})
    missing = [
        f"{locale}.json: {GROUP_KEY.format(group=group)}"
        for locale, catalog in _locales().items()
        for group in groups
        if not catalog.get(GROUP_KEY.format(group=group), "").strip()
    ]
    assert not missing, (
        "permission groups with no heading — the accordion renders the raw key:\n  "
        + "\n  ".join(sorted(missing))
    )


def test_no_permission_label_outlives_its_spec() -> None:
    """A renamed or deleted permission must not leave its old label behind.

    Only meaningful when every module ships in this deployment: a narrowed
    ``SCHAKL_ENABLED_MODULES`` legitimately leaves the absent modules' labels unclaimed.
    """
    default_modules = Settings.model_fields["enabled_modules"].default_factory()  # type: ignore[misc]
    if set(settings.enabled_modules) != set(default_modules):
        pytest.skip("enabled_modules is narrowed; absent modules' labels are not orphans")

    claimed = {spec.i18n_key for spec in all_permissions()}
    claimed |= {GROUP_KEY.format(group=spec.module) for spec in all_permissions()}
    orphans = sorted(
        key
        for key in _locales()["en"]
        if key.startswith("permissions.") and key not in claimed
    )
    assert not orphans, (
        "labels for permissions that no longer exist — remove them from every locale:\n  "
        + "\n  ".join(orphans)
    )
