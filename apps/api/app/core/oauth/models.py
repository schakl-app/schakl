"""OAuth 2.1 authorization-server tables (CLAUDE.md §12, docs/MCP.md).

Two tables, and what is *not* here is the point: **there is no access-token table**. What this
flow hands a client is an ordinary ``api_keys`` row (#20) — already tenant-scoped, already
carrying per-key permission scopes, already revocable, already capped by its owner's live
permissions on every request. An OAuth token table would be a second credential with a second
set of answers about what it may do, and the second answer is always the one that turns out to
be missing a rule.

So the pieces below are only the parts of the protocol the key system has no opinion about: who
the client is (:class:`OAuthClient`, RFC 7591 dynamic registration) and the ten minutes between
"the user pressed Toestaan" and "the client exchanged the code" (:class:`OAuthGrant`).

Both are org-scoped and RLS-forced like every domain table. A client registered on tenant A's
hostname is tenant A's — registration is unauthenticated, but it is never *tenant*-less, because
the hostname resolves the org before a row is written (§5).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base


class OAuthClient(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """A client that registered itself (RFC 7591) or was registered by hand.

    Dynamic registration is *unauthenticated* by design — it is how "Add connector" works in a
    chat client that has never heard of this instance — which makes it the one route here that
    writes a row for a stranger. Three things bound it: the caller's IP is rate-limited, the
    redirect URIs are validated to https-or-loopback before the row exists, and a client that
    never completes a flow is pruned by the same cron that prunes expired grants. None of that
    makes registration a privilege: a registered client can do nothing at all until a *person*
    signs in and consents.
    """

    __tablename__ = "oauth_clients"
    __table_args__ = (
        UniqueConstraint("org_id", "client_id", name="uq_oauth_clients_org_id_client_id"),
    )

    #: The public identifier, high-entropy so it is unguessable across tenants.
    client_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: SHA-256 of the client secret, or NULL for a public client (PKCE-only). Public is the
    #: normal case here: a desktop chat client cannot keep a secret, and OAuth 2.1 says so.
    secret_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: What the consent screen calls it. Client-supplied, so it is displayed as untrusted text.
    client_name: Mapped[str] = mapped_column(String(200), nullable=False)
    client_uri: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    logo_uri: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    #: Exact-match redirect targets. An exact list, never a pattern — a prefix match is how an
    #: open redirector is built by accident.
    redirect_uris: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    #: Set when a person registered it from the settings screen rather than over RFC 7591.
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: Cleared by the authorize step, so the pruner can tell "registered and abandoned" from
    #: "registered and in use" without joining the keys it minted.
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Revoking the client is the kill switch for every session it holds: the token path reads
    #: it (``api_keys.oauth_client_id``), so one row disconnects a connector everywhere.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OAuthGrant(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """One authorization code: what the user consented to, for ten minutes.

    Written at **approval**, never at the request: the consent screen validates its parameters
    against the client row and renders from that, so a person who opens the screen and closes it
    again leaves nothing behind. ``state`` is not stored for the same reason — it travels the
    form as a hidden field and comes back on the redirect, which is where the client expects it.

    **Single use is enforced by the database, not by this process.** A provider-style race is
    real here — a client that retries a slow token request has two exchanges in flight against
    two API replicas that share no memory, and "have we redeemed this yet?" followed by an
    insert leaves a window every retry enters. Redemption is therefore a conditional
    ``UPDATE … WHERE redeemed_at IS NULL RETURNING``: the loser of the race updates zero rows
    and is told the code is spent, which is also the correct answer to a *stolen* code
    (docs/PAYMENTS.md's rule, one protocol over).

    The code itself is never stored — only its SHA-256, like every other secret here.
    """

    __tablename__ = "oauth_grants"
    __table_args__ = (
        UniqueConstraint("org_id", "code_hash", name="uq_oauth_grants_org_id_code_hash"),
        Index("ix_oauth_grants_expires_at", "expires_at"),
    )

    client_pk: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("oauth_clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: The person who consented. The minted key acts as them, so it can never out-permission
    #: them — the cap is re-applied on every single request, not frozen at consent time.
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Echoed back on redemption and compared: RFC 6749 requires the same value both times.
    redirect_uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    #: PKCE (RFC 7636). ``S256`` only — OAuth 2.1 drops ``plain``, and accepting it would make
    #: the verifier a value an interceptor already has.
    code_challenge: Mapped[str] = mapped_column(String(128), nullable=False)
    #: The permission strings the *user* approved. Not what the client asked for: the consent
    #: screen may narrow the request, and never widens it.
    scopes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    #: RFC 8707: the ``/mcp`` URL (section segment included) this token will be bound to.
    resource: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Kept for the audit trail after redemption: which key this code became.
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True
    )
