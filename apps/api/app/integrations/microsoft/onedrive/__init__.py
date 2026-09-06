"""microsoft.onedrive — reference/link model over OneDrive and SharePoint libraries
(docs/MICROSOFT.md §5). Importing this package wires the folder-provisioning handlers onto
the bus."""

from __future__ import annotations

from app.core.events import subscribe
from app.integrations.microsoft.onedrive.events import (
    handle_company_created,
    handle_project_created,
)

subscribe("company.created", handle_company_created)
subscribe("project.created", handle_project_created)
