/**
 * The shapes the Cloudflare panel and settings screen read, taken from the generated client
 * (epic #278) — so a schema change breaks the build here rather than at runtime, and no
 * component re-declares a field the API owns.
 */
import type { components } from "$lib/core/api/schema";

export type DomainStatus = components["schemas"]["DomainStatusRead"];
export type ZoneRead = components["schemas"]["ZoneRead"];
export type RedirectRead = components["schemas"]["RedirectRead"];
export type PagesProject = components["schemas"]["PagesProjectRead"];
export type PagesLink = components["schemas"]["PagesLinkRead"];
export type AccountOption = components["schemas"]["AccountOption"];
export type AccountRead = components["schemas"]["AccountRead"];
export type DnsRecord = components["schemas"]["DnsRecordRead"];
export type ZoneRecords = components["schemas"]["ZoneRecords"];

/** The capabilities `verify` can observe, in the order the settings screen lists them. */
export const CAPABILITIES = [
  "token_valid",
  "accounts_read",
  "zones_read",
  "pages_read",
] as const;
