"""meetings module (CLAUDE.md §6) — a recorded meeting into a transcript, minutes and tasks.

Importing this package self-registers the module (router, company panel, permissions, worker
functions, crons, i18n namespace) into the shared registry.

**A module, not an integration** (§6a): with every speech vendor gone the recordings, the
transcripts and the minutes are still here, and the recorder still records — it is merely
poorer. What it *talks to* is the tenant's own speech and chat provider, through the AI core,
exactly as dictation does.
"""

from __future__ import annotations

from arq import cron

from app.core.trash import register_trash_dependent
from app.modules.meetings.jobs import (
    meetings_process,
    meetings_reap_stale,
    meetings_sweep_audio,
)
from app.modules.meetings.mcp import MEETING_MCP_TOOLS
from app.modules.meetings.panels import meetings_company_panel
from app.modules.meetings.permissions import MEETING_PERMISSIONS
from app.modules.meetings.router import router
from app.modules.meetings.trash import MEETING_TRASH_DEPENDENTS
from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="meetings",
    router=router,
    i18n_namespace="meetings",
    # Licensed module (issue #137): past expiry+grace it goes read-only — the minutes stay
    # readable, no new recording is opened and the worker stops taking runs.
    sku="meetings",
    panels=[meetings_company_panel],
    permissions=MEETING_PERMISSIONS,
    # Curated read tools beside the generated route tools (§12): the register, the
    # words, the minutes — what an agent asks about a meeting.
    mcp_tools=MEETING_MCP_TOOLS,
    # The pipeline runs in the worker: minutes of provider time per meeting, and nobody is
    # waiting on a request for it.
    worker_functions=[meetings_process],
    cron_jobs=[
        # A run claimed by a worker that is no longer there has no other way back (#300).
        cron(meetings_reap_stale, minute={5, 20, 35, 50}),
        # The recording's retention: the words stay, the audio goes.
        cron(meetings_sweep_audio, hour={3}, minute={40}),
    ],
)

registry.register(module)

for _dependent in MEETING_TRASH_DEPENDENTS:
    register_trash_dependent("company", _dependent)
