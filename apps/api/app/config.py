"""Application configuration (CLAUDE.md §3, §7).

All settings come from the environment, prefixed ``SCHAKL_`` (no secrets in code).
The internal codename is ``schakl``; the *user-facing* brand is per-tenant and never set here.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_messages_dir() -> Path:
    """Locate the shared ``messages/`` catalogs without assuming a fixed directory depth.

    Works both in the source tree (repo-root/messages) and in the container image (where the
    app is copied to /app and messages to /app/messages). Overridable via SCHAKL_MESSAGES_DIR.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "messages"
        if candidate.is_dir():
            return candidate
    return here.parent / "messages"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SCHAKL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Runtime ---
    environment: str = "development"
    debug: bool = True

    # --- Build stamp (injected at image build; left at the sentinels in a source tree) ---
    # See apps/api/Dockerfile and .github/workflows/release.yml.
    version: str = "0.0.0+dev"
    git_sha: str = "unknown"
    built_at: str | None = None

    # --- Update check (instance-level; the box makes the outbound call, not the tenant) ---
    # A daily, unauthenticated GET to the GitHub Releases API. Nothing is sent about this
    # instance. Set false to disable all outbound update traffic (docs/DEPLOY.md).
    update_check_enabled: bool = True
    update_check_repo: str = "schakl-app/schakl"

    # --- File storage (issue #123) ---
    # "local" writes under storage_path (a named volume in Compose); "gdrive"/"s3" are the
    # future backends the seam exists for. Callers depend on the interface, never the path.
    storage_backend: str = "local"
    storage_path: str = "/data/storage"
    # Upload guardrails: bytes, and an allow-list of content types (images, pdf, plain text,
    # archives, office docs — the practical attachment set; extend per deployment via env).
    upload_max_bytes: int = 10 * 1024 * 1024
    upload_allowed_types: list[str] = Field(
        default_factory=lambda: [
            "image/png", "image/jpeg", "image/webp", "image/gif", "image/svg+xml",
            "image/x-icon", "image/vnd.microsoft.icon",
            "application/pdf", "text/plain", "text/csv",
            "application/zip",
            "application/msword", "application/vnd.ms-excel",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ]
    )

    # --- Database / cache ---
    # Async SQLAlchemy URL (asyncpg driver). The app connects as a NON-superuser role so
    # Postgres RLS is enforced (superusers bypass RLS) — see infra/db init and CLAUDE.md §5.
    database_url: str = "postgresql+asyncpg://schakl_app:schakl_app@localhost:5432/schakl"
    redis_url: str = "redis://localhost:6379/0"

    # --- Tenancy / white-label ---
    # <slug>.<base_domain> or a custom domain resolves to an org (CLAUDE.md §7).
    base_domain: str = "localhost"
    # Config-level modules mounted by main.py; per-tenant enablement lives in org_settings.
    enabled_modules: list[str] = Field(
        default_factory=lambda: [
            "companies", "contacts", "tasks", "projects", "time", "leave", "notifications",
            "domains", "hosting", "websites", "subscriptions", "automation", "interactions",
            "google",
        ]
    )
    default_locale: str = "nl"
    supported_locales: list[str] = Field(default_factory=lambda: ["nl", "en"])
    # Fallback display/scheduling timezone for an org that has not set its own (CLAUDE.md §8).
    # Per-tenant value lives on org_settings.timezone; this is only the seed/fallback. An IANA
    # zone name — validated on write against the platform's zoneinfo database.
    default_timezone: str = "Europe/Amsterdam"

    # --- Outbound hooks (issues #17, #27, #96) ---
    # Tenant-configured outbound targets (automation webhooks, notification transports) refuse
    # private/loopback/link-local addresses by default — a self-hosted box must not let a rule
    # author probe the internal network (SSRF). Set true only for a trusted LAN deployment
    # whose n8n/Uptime-Kuma etc. live on private addresses.
    allow_private_notification_targets: bool = False

    # --- Auth ---
    secret_key: str = "change-me-in-production-please-32bytes-min"
    #: Key material for encrypting secrets at rest (notification channel URLs #17, Google refresh
    #: tokens). Falls back to ``secret_key`` when unset, so a single-secret install still works;
    #: set ``SCHAKL_ENCRYPTION_KEY`` to rotate it independently of the auth secret.
    encryption_key: str | None = None
    # Cookie transport is used by the SSR web app.
    auth_cookie_name: str = "schakl_auth"
    auth_cookie_secure: bool = False  # True behind HTTPS in production
    auth_token_lifetime_seconds: int = 60 * 60 * 24 * 7
    allow_registration: bool = True
    # OIDC / SSO is **per-org tenant data** (issue #76): everything lives on the RLS-forced
    # ``org_auth_settings`` row and is managed under Instellingen → SSO — the old
    # ``SCHAKL_OIDC_*`` env vars are retired (the #76 migration seeded them into the DB once).
    # This one flag remains: the operator break-glass that re-enables local password login
    # regardless of any org's "enforce SSO" toggle, so a broken IdP can never lock a tenant
    # out of its own instance (docs/SSO.md).
    force_local_login: bool = False

    # --- Google Workspace OAuth (stub for P3) ---
    google_client_id: str | None = None
    google_client_secret: str | None = None

    # --- i18n ---
    # Shared message catalogs (single source of truth with the web app).
    messages_dir: Path = Field(default_factory=_default_messages_dir)

    # --- MCP server (CLAUDE.md §12) ---
    # Streamable HTTP at /mcp: every /api/v1 operation as a tool, authenticated with the
    # platform's API keys (per-key permission scopes, #20). Disable to remove the surface.
    mcp_enabled: bool = True

    # --- Entitlements / licensing (issue #137) ---
    # Ed25519 public key (base64url, raw 32 bytes) that license keys are verified against.
    # Verification is fully offline — a self-hosted box never needs our infrastructure to
    # boot or keep running. The default is the production signing key's public half; tests
    # and a future key rotation override it via SCHAKL_LICENSE_PUBLIC_KEY.
    license_public_key: str = "wXZZDCEuAVzK82CoaPckClnLtSnNWk1jpeuDbHk1RwQ"
    # Days an already-enabled licensed module keeps working without any license — the
    # upgrade path for installs that enabled it before licensing existed (#137).
    license_bootstrap_grace_days: int = 14

    # --- Instance administration (issue #26) ---
    # The cross-tenant admin surface is pure attack surface on a single-tenant box, so it
    # ships **disabled**; SCHAKL_INSTANCE_ADMIN_ENABLED=true opens it to instance owners
    # (users.is_superuser — the operator principal, distinct from an org's `owner` role).
    instance_admin_enabled: bool = False
    # Upper bound for a single impersonation grant; every grant is audited and time-boxed.
    impersonation_max_minutes: int = 60

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}

    @property
    def is_stamped_build(self) -> bool:
        """True when a real version was baked in — i.e. this is a released image, not a checkout.

        An unstamped tree sorts below every release, so the update check would always claim an
        update is available. It stays quiet instead.
        """
        return self.version != "0.0.0+dev"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
