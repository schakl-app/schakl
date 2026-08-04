/**
 * The shapes the oxxa panel and settings screen read, taken from the generated client
 * (issue #296) — so a schema change breaks the build here rather than at runtime, and no
 * component re-declares a field the API owns.
 *
 * The API's account schemas are named `OxxaAccount*` rather than `Account*` because `cloudflare`
 * already publishes an `AccountRead`/`AccountCreate`/…, and a collision makes FastAPI qualify
 * **both** sides — renaming *this* module's half is what keeps `cloudflare/types.ts` reading the
 * plain name it always read. The prefix is stripped again here, once, so every component in this
 * module reads the short alias.
 */
import type { components } from "$lib/core/api/schema";

export type AccountRead = components["schemas"]["OxxaAccountRead"];
export type AccountOption = components["schemas"]["OxxaAccountOption"];
export type RegistrarDomain = components["schemas"]["RegistrarDomainRead"];
export type RegistrarStatus = components["schemas"]["DomainRegistrarStatus"];
export type NameserverPushResult = components["schemas"]["NameserverPushResult"];

/** What the panel's `load` hands its component. */
export interface OxxaPanelData {
  status: RegistrarStatus | null;
  accounts: AccountOption[];
}

/**
 * The size of an OXXA nameserver group, mirroring `MIN_NAMESERVERS`/`MAX_NAMESERVERS` in the
 * API's `client.py`. Used only to keep the form from posting an obvious non-answer; the API
 * validates it again, and the API is the boundary (CLAUDE.md §15).
 */
export const MIN_NAMESERVERS = 2;
export const MAX_NAMESERVERS = 6;

/** Split the textarea into hostnames. Whitespace *or* commas: people paste both. */
export function parseNameservers(raw: string): string[] {
  const seen: string[] = [];
  for (const part of raw.split(/[\s,;]+/)) {
    const host = part.trim().toLowerCase().replace(/\.$/, "");
    if (host && !seen.includes(host)) seen.push(host);
  }
  return seen;
}
