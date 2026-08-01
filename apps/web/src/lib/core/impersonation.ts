/** The impersonation-grant cookie (issue #26) — set next to, never instead of, the session. */
export const IMPERSONATION_COOKIE = "schakl_impersonate";

/**
 * Where to return an operator who arrived through a cross-host handoff (#288).
 *
 * Set on the tenant's hostname when the ticket is redeemed and read when the impersonation
 * stops, so ending it lands back on the console instead of on a login screen for an account
 * that does not exist here. It holds an origin the **API** derived from its own configuration —
 * never a value from a query parameter, which is what keeps it out of open-redirect territory.
 */
export const HANDOFF_RETURN_COOKIE = "schakl_impersonate_return";

/**
 * Where to put a staff member back after they stop being signed in as a contact (#296).
 *
 * Holds a **path on this same origin** — the contact they came from — because a portal
 * impersonation never leaves the tenant's host, so there is nothing to say about the origin and
 * nothing an attacker could steer. Written when the impersonation starts, read and dropped when
 * it stops; anything that is not a single-slash-prefixed path is ignored in favour of "/".
 */
export const PORTAL_RETURN_COOKIE = "schakl_impersonate_back";

/** A stored return path, or "/" — never a value that could leave this origin. */
export function safeReturnPath(value: string | undefined | null): string {
  return value && /^\/[^/\\]/.test(value) ? value : "/";
}
