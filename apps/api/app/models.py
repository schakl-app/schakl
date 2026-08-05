"""Aggregate model imports so ``Base.metadata`` is complete (for Alembic autogenerate).

Imports core models plus each enabled module's ``models`` submodule. Used by the Alembic env,
never by the running app (the app discovers modules in ``main.py``).
"""

from __future__ import annotations

import importlib
import importlib.util

from app.config import settings
from app.core.activity.models import ActivityLog  # noqa: F401
from app.core.ai.models import AIReport, AISettings, AIUsage  # noqa: F401
from app.core.apikeys.models import ApiKey, ServiceAccount  # noqa: F401
from app.core.auth.models import User  # noqa: F401
from app.core.cloud.models import InstanceApiKey, ServiceAccessGrant  # noqa: F401
from app.core.customfields.models import CustomFieldDefinition  # noqa: F401
from app.core.email.models import EmailSettings, OrgEmailTemplate  # noqa: F401
from app.core.instance.impersonation import ImpersonationHandoff  # noqa: F401
from app.core.models import InstanceAuditLog, Membership, Org, OrgSettings  # noqa: F401
from app.core.permissions.models import (  # noqa: F401
    MembershipRole,
    Role,
    RoleAuditLog,
    RolePermission,
)
from app.core.providers.models import Provider  # noqa: F401
from app.core.storage.models import FileBlob, StoredFile  # noqa: F401
from app.db import Base  # noqa: F401

for _name in settings.enabled_modules:
    # A module need not own a table. ``portal`` is the proof: it manages client logins against
    # rows another module owns, reached through the subject seam (``app/core/portal.py``), so
    # shipping it an empty ``models.py`` would be a file that lies about what the module is.
    # ``find_spec`` rather than catching ``ModuleNotFoundError`` on purpose — the latter also
    # swallows a genuinely broken import *inside* a module's models, which is exactly the
    # failure this aggregator exists to surface.
    if importlib.util.find_spec(f"app.modules.{_name}.models") is not None:
        importlib.import_module(f"app.modules.{_name}.models")
