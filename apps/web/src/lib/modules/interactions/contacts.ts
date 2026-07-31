/**
 * The contact roster an interaction form picks from, fetched once per scope (#290).
 *
 * Several of these forms live on one page — the panel's create modal, its edit modal, the
 * page's own create modal — and each used to fire its own `/contacts?limit=200` on mount,
 * for the same rows. The cache below is keyed by scope and holds the *promise*, so forms that
 * mount together share one flight rather than racing three identical requests
 * (docs/PERFORMANCE.md).
 *
 * Module scope is safe here because this only ever runs in the browser, in one user's tab —
 * unlike a server-side cache, which would be a tenant-isolation bug (Golden Rule 1).
 */
export interface ContactRow {
  id: string;
  first_name: string;
  last_name?: string | null;
  email?: string | null;
  companies?: { name: string }[];
}

const cache = new Map<string, Promise<ContactRow[]>>();

async function get(url: string): Promise<ContactRow[]> {
  try {
    const response = await fetch(url, { headers: { accept: "application/json" } });
    return response.ok ? ((await response.json()).items ?? []) : [];
  } catch {
    return [];
  }
}

/**
 * `companyId` empty = the whole org. A scoped fetch that comes back empty falls back to the
 * unscoped roster: an org that never linked its contacts to clients would otherwise see an
 * empty picker and conclude the feature is broken.
 */
export function contactsForScope(companyId: string): Promise<ContactRow[]> {
  const cached = cache.get(companyId);
  if (cached) return cached;
  const scope = companyId ? `&company_id=${companyId}` : "";
  const request = get(`/api/v1/contacts?limit=200&count=false&sort=first_name${scope}`).then(
    async (items) => (items.length === 0 && scope ? await contactsForScope("") : items),
  );
  cache.set(companyId, request);
  return request;
}

/** Called after an inline create so the next picker sees the new person. */
export function forgetContacts(): void {
  cache.clear();
}
