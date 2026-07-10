/**
 * Typed API client (CLAUDE.md §3, Golden Rule 6).
 *
 * The web app talks to the API **only** through this generated-schema client — never to the
 * database. Used server-side (SSR loads, form actions): it forwards the caller's auth cookie
 * and the tenant hostname (`X-Forwarded-Host`) so the API resolves the same tenant + user.
 */
import createClient, { type Client } from "openapi-fetch";
import { env } from "$env/dynamic/private";

import type { paths } from "./schema";

export type ApiClient = Client<paths>;

export function apiBaseUrl(): string {
  return env.SCHAKL_API_URL ?? "http://localhost:8000";
}

export interface ApiClientOptions {
  fetch: typeof fetch;
  cookie?: string | null;
  host?: string | null;
}

export function createApiClient(opts: ApiClientOptions): ApiClient {
  const headers: Record<string, string> = { accept: "application/json" };
  if (opts.cookie) headers["cookie"] = opts.cookie;
  if (opts.host) headers["x-forwarded-host"] = opts.host;
  return createClient<paths>({
    baseUrl: apiBaseUrl(),
    fetch: opts.fetch,
    headers,
  });
}
