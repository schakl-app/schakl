"""Aggregate model imports so ``Base.metadata`` is complete (for Alembic autogenerate).

Imports core models plus each enabled module's ``models`` submodule. Used by the Alembic env,
never by the running app (the app discovers modules in ``main.py``).
"""

from __future__ import annotations

import importlib

from app.config import settings
from app.core.auth.models import User  # noqa: F401
from app.core.customfields.models import CustomFieldDefinition  # noqa: F401
from app.core.models import Membership, Org, OrgSettings  # noqa: F401
from app.db import Base  # noqa: F401

for _name in settings.enabled_modules:
    importlib.import_module(f"app.modules.{_name}.models")
