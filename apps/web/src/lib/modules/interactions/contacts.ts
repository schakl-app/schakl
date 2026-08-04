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
 * `companyId` empty = the whole org; a client id = **that client's people, and nobody else's**.
 *
 * There is deliberately no widen-to-the-org fallback when a client's roster comes back empty.
 * One used to live here, on the reasoning that an empty picker reads as broken — but what it
 * actually did was hand every client with no linked contacts the agency's entire address book,
 * so a contactmoment filed to that client could name a person at a different one, and no screen
 * afterwards flags that. "This client has nothing yet" is a real state, and it is answerable
 * where it is asked: the picker's ＋ opens the full create dialog pre-linked to this same client
 * (#247, docs/UX.md), which is what turns the empty roster into one row instead of a dead end.
 */
export function contactsForScope(companyId: string): Promise<ContactRow[]> {
  const cached = cache.get(companyId);
  if (cached) return cached;
  const scope = companyId ? `&company_id=${encodeURIComponent(companyId)}` : "";
  const request = get(`/api/v1/contacts?limit=200&count=false&sort=first_name${scope}`);
  cache.set(companyId, request);
  return request;
}

/** Called after an inline create so the next picker sees the new person. */
export function forgetContacts(): void {
  cache.clear();
}
