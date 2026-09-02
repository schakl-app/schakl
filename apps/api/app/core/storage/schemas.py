"""Pydantic schemas for stored files (issue #123)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class StoredFileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    # Which backend holds the bytes (#190) and under which opaque key (`<org_id>/<file_id>`,
    # no secret) — surfaced for ops: "which rows still live on the volume?" is answerable.
    backend: str
    storage_key: str
    filename: str
    content_type: str
    size_bytes: int
    entity_type: str | None
    entity_id: uuid.UUID | None
    # Set means the file is part of its entity's *body* rather than attached to it — an
    # e-mail's `cid:` image. Listed only when explicitly asked for.
    content_id: str | None = None
    #: May a client-portal login read this attachment (see the model)? Off by default.
    client_visible: bool = False
    created_by_user_id: uuid.UUID | None
    created_at: datetime


class StoredFileUpdate(BaseModel):
    """The one editable fact about a stored file: whether the client may read it."""

    client_visible: bool


class InlineUpload(BaseModel):
    """A file carried **inside a JSON body** rather than as a multipart part.

    The multipart route is the right one for a browser, and the wrong one for everything the
    generated MCP surface and a JSON-only automation can send (docs/MCP.md): a tool call is a
    JSON document, so a route whose body is ``multipart/form-data`` answers every agent with
    ``422 file: field required``. Base64 costs a third more bytes and buys a file any JSON
    client can post — the shape Gmail, Drive and every other JSON API use for the same reason.
    """

    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=120)
    #: Standard base64 (RFC 4648 §4), padding optional; a ``data:`` URL prefix is also accepted.
    data: str = Field(min_length=1)
    entity_type: str | None = None
    entity_id: uuid.UUID | None = None
    #: Store as part of the entity's **body** rather than as an attachment (``content_id``):
    #: an image referenced by an ``![alt](file:<id>)`` marker inside a description or comment
    #: renders in the text and must not double up in the attachment strip.
    inline: bool = False
    client_visible: bool = False
