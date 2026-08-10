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
  // Its own token permission, and its own authority (#298): a token that reads zones every
  // night still knows nothing about who pays for a registration. Listing it here is what tells
  // an admin *why* their domains still invoice as they always did.
  "registrar_read",
  // The two the domain page's own buttons use, and the two this list used to omit — which is
  // how an admin came to read ✓ down every line and still get a token error at "Opslaan" on a
  // redirect. Zone-scoped, so they are *absent* rather than false before the first sync: see
  // `capabilityState`.
  "dns_read",
  "redirect_read",
] as const;

/**
 * Whether a capability was granted, refused, or never asked about.
 *
 * The API omits a zone-scoped key entirely when it had no zone to address the probe at, and the
 * distinction is the whole point of the list: "we did not look" rendered as "niet toegekend"
 * sends an admin to widen a token over a permission nobody has tested.
 */
export function capabilityState(
  capabilities: Record<string, boolean> | null | undefined,
  key: string,
): "granted" | "missing" | "unprobed" {
  const value = capabilities?.[key];
  if (value === undefined) return "unprobed";
  return value ? "granted" : "missing";
}
