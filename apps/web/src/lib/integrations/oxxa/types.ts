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

/** One hostname, compared the way DNS reads it: case-insensitive, root dot optional. */
export function normalizeNameserver(host: string): string {
  return host.trim().toLowerCase().replace(/\.$/, "");
}

/** Split the textarea into hostnames. Whitespace *or* commas: people paste both. */
export function parseNameservers(raw: string): string[] {
  const seen: string[] = [];
  for (const part of raw.split(/[\s,;]+/)) {
    const host = normalizeNameserver(part);
    if (host && !seen.includes(host)) seen.push(host);
  }
  return seen;
}

/**
 * Whether two delegations are the same one. **Order is not part of it**: a registrar returns its
 * nameservers in whatever order it stores them, and a set that differs only in order is the same
 * delegation — comparing the joined strings would call an unchanged domain changed.
 *
 * An empty side is never "the same": nothing known cannot match something known, and treating it
 * as agreement is how a panel would fall silent about a delegation it has not read yet.
 */
export function sameNameservers(
  a: readonly string[] | null | undefined,
  b: readonly string[] | null | undefined,
): boolean {
  const left = new Set((a ?? []).map(normalizeNameserver).filter(Boolean));
  const right = new Set((b ?? []).map(normalizeNameserver).filter(Boolean));
  if (left.size === 0 || left.size !== right.size) return false;
  return [...left].every((host) => right.has(host));
}
