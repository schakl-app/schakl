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
  /** A contact-linked (client-portal) login (#193): renders the reduced portal shell. */
  isPortal: boolean;
  /** Instance owner (users.is_superuser) regardless of the admin-surface flag — gates
   *  license management (issue #137). */
  isInstanceOwner: boolean;
  /** Set while someone is signed in as this user — drives the banner. */
  impersonatedBy: string | null;
  impersonationExpiresAt: string | null;
  /** Which impersonation: `instance` (issue #26) or `portal` (#296, agency staff signed in as a
   *  client's contact). The banner is the same; the stop goes to a different endpoint, which is
   *  why the web has to know. Never taken from the form — the API says which one this is. */
  impersonationKind: string | null;
  /**
   * This portal impersonation is running as *less* than the client actually holds, because the
   * impersonator does not hold it either (#266). The banner says so: signing in as a client is
   * for seeing what they see, so an unlabelled partial view is a screen that lies — staff would
   * report "their invoices are missing" about a client who has them.
   */
  impersonationNarrowed: boolean;
  /** AI features usable in this tenant (epic #131). Empty until an admin configures a
   *  provider under Instellingen → AI — "off means invisible", so an empty list renders
   *  no AI affordance anywhere. */
  aiFeatures: string[];
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
    appIconUrl: data.app_icon_url ?? null,
    primaryColor: data.primary_color,
    accentColor: data.accent_color,
    defaultLocale: data.default_locale,
    timezone: data.timezone,
    currency: data.currency,
    defaultCountry: data.default_country ?? "NL",
    tabTitleTemplate: data.tab_title_template ?? null,
    enabledModules: data.enabled_modules,
    demoMode: data.demo_mode ?? false,
    demoResetMinutes: data.demo_reset_minutes ?? 60,
    resolved: true,
    suspended: data.suspended,
    endsWarningUntil: data.ends_warning_until ?? null,
    canonicalHost: data.canonical_host ?? null,
    domainUnhealthy: data.domain_unhealthy ?? false,
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
    permissions: data.permissions ?? [],
    locale: data.locale ?? null,
    avatarUrl: data.avatar_url ?? null,
    customAvatarUrl: data.custom_avatar_url ?? null,
    isInstanceAdmin: data.is_instance_admin ?? false,
    isPortal: data.is_portal ?? false,
    isInstanceOwner: data.is_instance_owner ?? false,
    impersonatedBy: data.impersonated_by ?? null,
    impersonationExpiresAt: data.impersonation_expires_at ?? null,
    impersonationKind: data.impersonation_kind ?? null,
    impersonationNarrowed: data.impersonation_narrowed ?? false,
    aiFeatures: data.ai_features ?? [],
  };
}
