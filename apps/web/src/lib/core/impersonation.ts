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
