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
            "companies", "contacts", "tasks", "projects", "time", "leave", "notifications"
        ]
    )
    default_locale: str = "nl"
    supported_locales: list[str] = Field(default_factory=lambda: ["nl", "en"])
    # Fallback display/scheduling timezone for an org that has not set its own (CLAUDE.md §8).
    # Per-tenant value lives on org_settings.timezone; this is only the seed/fallback. An IANA
    # zone name — validated on write against the platform's zoneinfo database.
    default_timezone: str = "Europe/Amsterdam"

    # --- Auth ---
    secret_key: str = "change-me-in-production-please-32bytes-min"
    #: Key material for encrypting secrets at rest (notification channel URLs #17, Google refresh
    #: tokens). Falls back to ``secret_key`` when unset, so a single-secret install still works;
    #: set ``SCHAKL_ENCRYPTION_KEY`` to rotate it independently of the auth secret.
    encryption_key: str | None = None
    #: Allow notification webhook URLs pointing at private/link-local addresses (#17). Off by
    #: default (SSRF guard); a self-hosted admin may enable it for an in-network endpoint.
    allow_private_notification_targets: bool = False
    # Cookie transport is used by the SSR web app.
    auth_cookie_name: str = "schakl_auth"
    auth_cookie_secure: bool = False  # True behind HTTPS in production
    auth_token_lifetime_seconds: int = 60 * 60 * 24 * 7
    allow_registration: bool = True

    # --- OIDC (optional; when enforced, local login is disabled — CLAUDE.md §3, P0) ---
    oidc_enabled: bool = False
    oidc_enforced: bool = False
    oidc_name: str = "sso"
    oidc_discovery_url: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    # On SSO login, auto-grant a membership in the resolved org so JIT-provisioned users aren't
    # locked out (they'd otherwise have an identity but no org access). Disable to require an
    # explicit invite first.
    oidc_auto_provision_membership: bool = True
    oidc_default_role: str = "member"

    # --- Google Workspace OAuth (stub for P3) ---
    google_client_id: str | None = None
    google_client_secret: str | None = None

    # --- i18n ---
    # Shared message catalogs (single source of truth with the web app).
    messages_dir: Path = Field(default_factory=_default_messages_dir)

    # --- Instance administration (issue #26) ---
    # The cross-tenant admin surface is pure attack surface on a single-tenant box, so it
    # ships **disabled**; SCHAKL_INSTANCE_ADMIN_ENABLED=true opens it to instance owners
    # (users.is_superuser — the operator principal, distinct from an org's `owner` role).
    instance_admin_enabled: bool = False
    # Upper bound for a single impersonation grant; every grant is audited and time-boxed.
    impersonation_max_minutes: int = 60

    @property
    def local_login_enabled(self) -> bool:
        """Local username/password login is on unless OIDC is *enforced*."""
        return not self.oidc_enforced

    @property
    def oidc_missing_settings(self) -> list[str]:
        """The env vars the OIDC flow needs but that are unset (empty string counts as unset)."""
        required = {
            "SCHAKL_OIDC_ENABLED": self.oidc_enabled,
            "SCHAKL_OIDC_DISCOVERY_URL": self.oidc_discovery_url,
            "SCHAKL_OIDC_CLIENT_ID": self.oidc_client_id,
            "SCHAKL_OIDC_CLIENT_SECRET": self.oidc_client_secret,
        }
        return [name for name, value in required.items() if not value]

    @property
    def oidc_configured(self) -> bool:
        """Single gate for the whole OIDC surface (issue #6).

        Both the route mount (``build_oidc_router``) and the login page's SSO button
        (``/meta/modules``) key off this one property, so the button can never point at a
        route that was not mounted.
        """
        return not self.oidc_missing_settings

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
