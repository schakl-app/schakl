"""Request/response models for the portal module (CLAUDE.md §6)."""

from __future__ import annotations

import uuid
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


class PortalLoginClient(BaseModel):
    """A client a login belongs to. Two fields, because a register prints a name and links it."""

    id: uuid.UUID
    name: str


class PortalLoginRow(BaseModel):
    """One client login on the register (#406) — *"who at our clients can sign in?"*

    Never ``status: "none"``: a subject with no login is the absence of a row, not a row saying
    so. And deliberately **not** an editor's payload — the person is edited on their own record,
    which is what ``entity_type`` + ``subject_id`` link to; what lives here is the access.

    No total beside it, on purpose. The list *is* the count, so the two cannot disagree — a
    hand-built ``count()`` is exactly how a screen comes to say "2" over a list of one (#285).
    """

    entity_type: str
    subject_id: uuid.UUID
    #: The account behind the login. Present on every row by construction (a row exists because
    #: a login does), and what a caller needs to tell two people with one name apart.
    user_id: uuid.UUID
    name: str | None = None
    email: str
    status: Literal["invited", "active", "disabled"]
    clients: list[PortalLoginClient] = Field(default_factory=list)


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
