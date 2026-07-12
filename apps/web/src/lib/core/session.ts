/**
 * Server-side session + tenant helpers (CLAUDE.md §5, §7).
 *
 * Everything goes through the typed API client; the web never reads the database. Tenant and
 * user are resolved per request from the forwarded hostname + auth cookie.
 */
import { createApiClient, type ApiClient } from "./api/client";
import { DEFAULT_THEME, type OrgTheme } from "./theme";

export interface SessionUser {
  id: string;
  email: string;
  full_name: string | null;
  /** Membership role within the resolved tenant (owner/admin/member/client). */
  role: string;
  /** True for owner/admin. DEPRECATED (issue #19) — use `can(...)`; dropped next release. */
  canManage: boolean;
  /** Effective permissions: the union over every role held. `["*"]` for an owner. */
  permissions: string[];
  /** Personal display-language preference (null → org default). */
  locale: string | null;
  /** Effective avatar (#122): personal override → OIDC picture → null (initials). */
  avatarUrl: string | null;
  /** The stored override alone, for Settings → Account. */
  customAvatarUrl: string | null;
  /** Instance owner with the instance-admin surface enabled (issue #26). */
  isInstanceAdmin: boolean;
  /** Set while an instance owner impersonates this user — drives the banner. */
  impersonatedBy: string | null;
  impersonationExpiresAt: string | null;
}

// Minimal shape shared by SvelteKit load events and action/request events.
export interface ApiEvent {
  fetch: typeof fetch;
  request: Request;
}

export function apiFor(event: ApiEvent): ApiClient {
  return createApiClient({
    fetch: event.fetch,
    cookie: event.request.headers.get("cookie"),
    host: event.request.headers.get("host"),
  });
}

export async function fetchTenant(event: ApiEvent): Promise<OrgTheme> {
  const { data } = await apiFor(event).GET("/api/v1/meta/tenant");
  if (!data) return DEFAULT_THEME; // resolved: false — unknown host or fresh install
  return {
    brandName: data.brand_name,
    showBrandName: data.show_brand_name,
    logoUrl: data.logo_url,
    faviconUrl: data.favicon_url,
    primaryColor: data.primary_color,
    accentColor: data.accent_color,
    defaultLocale: data.default_locale,
    timezone: data.timezone,
    currency: data.currency,
    tabTitleTemplate: data.tab_title_template ?? null,
    enabledModules: data.enabled_modules,
    resolved: true,
    suspended: data.suspended,
  };
}

export async function fetchUser(event: ApiEvent): Promise<SessionUser | null> {
  // /meta/me resolves the user *within the tenant*, so it also carries the membership role.
  const { data } = await apiFor(event).GET("/api/v1/meta/me");
  if (!data) return null;
  return {
    id: data.id,
    email: data.email,
    full_name: data.full_name ?? null,
    role: data.role,
    canManage: data.can_manage,
    permissions: data.permissions ?? [],
    locale: data.locale ?? null,
    avatarUrl: data.avatar_url ?? null,
    customAvatarUrl: data.custom_avatar_url ?? null,
    isInstanceAdmin: data.is_instance_admin ?? false,
    impersonatedBy: data.impersonated_by ?? null,
    impersonationExpiresAt: data.impersonation_expires_at ?? null,
  };
}
