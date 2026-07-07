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
  is_superuser: boolean;
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
  if (!data) return DEFAULT_THEME;
  return {
    brandName: data.brand_name,
    logoUrl: data.logo_url,
    faviconUrl: data.favicon_url,
    primaryColor: data.primary_color,
    accentColor: data.accent_color,
    defaultLocale: data.default_locale,
    enabledModules: data.enabled_modules,
  };
}

export async function fetchUser(event: ApiEvent): Promise<SessionUser | null> {
  const { data } = await apiFor(event).GET("/api/v1/users/me");
  if (!data) return null;
  return {
    id: data.id,
    email: data.email,
    full_name: data.full_name ?? null,
    is_superuser: data.is_superuser,
  };
}
