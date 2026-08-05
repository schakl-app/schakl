"""Request/response models for the portal module (CLAUDE.md §6)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

#: ``none`` — the subject has no login. ``invited`` — the invite is out but the mailbox has
#: never been used (setting the password through the emailed link is what verifies it).
#: ``active`` — signed in at least once. ``disabled`` — the account is kept but refuses login.
PortalStatus = Literal["none", "invited", "active", "disabled"]


class PortalLoginState(BaseModel):
    """Where one subject's client login stands. Named for the module, not for ``contacts``:
    the subject is whatever registered a provider (``app/core/portal.py``)."""

    entity_type: str
    subject_id: str
    status: PortalStatus = "none"
    email: str | None = None
    #: The address an invite *would* go to when there is no login yet — so the UI can say
    #: "invite jan@klant.nl" rather than offering a button that 422s on an empty mailbox.
    invite_email: str | None = None
    invite_email_sent: bool | None = None
    invite_email_error: str | None = None


class PortalImpersonateRequest(BaseModel):
    #: Clamped again server-side by ``SCHAKL_IMPERSONATION_MAX_MINUTES``; this is only the ask.
    minutes: int = Field(default=30, ge=1, le=24 * 60)


class PortalImpersonateResponse(BaseModel):
    cookie: str
    token: str
    expires_at: datetime
    #: Who the caller is about to become — so the confirmation is about a person, not an id.
    target_email: str
    target_name: str | None = None
