"""google.drive — reference/link model over the Shared Drive (docs/GOOGLE.md §5, issue #21).

Importing this package wires the folder-provisioning handlers onto the bus.
"""

from __future__ import annotations

from app.core.events import subscribe
from app.modules.google.drive.events import handle_company_created, handle_project_created

subscribe("company.created", handle_company_created)
subscribe("project.created", handle_project_created)
