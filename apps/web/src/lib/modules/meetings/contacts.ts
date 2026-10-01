/**
 * The meeting's client's people, fetched once per client and shared by every picker on the page.
 *
 * Three controls ask the same question — the roster's contact picker, an action item's "who took
 * this on", and the task sheet's assignee — and each used to answer it differently: the roster
 * fetched the client's contacts, while the other two offered only the contacts already *on the
 * roster*, so a promise made by somebody who was not at the table (or was typed in as a bare
 * name) could not be given to them, and an item the AI box had given to one printed as nobody.
 * The cache holds the *promise*, so pickers that mount together share one request
 * (docs/PERFORMANCE.md). Browser-only, one user's tab: never a server-side cache.
 */
export interface ClientContact {
  id: string;
  name: string;
}

const cache = new Map<string, Promise<ClientContact[]>>();

interface ContactRow {
  id: string;
  first_name: string;
  last_name?: string | null;
}

async function fetchContacts(companyId: string): Promise<ClientContact[]> {
  try {
    const response = await fetch(
      `/api/v1/contacts?limit=200&count=false&sort=first_name&company_id=${encodeURIComponent(companyId)}`,
      { headers: { accept: "application/json" } },
    );
    if (!response.ok) return [];
    const rows: ContactRow[] = (await response.json()).items ?? [];
    return rows.map((c) => ({
      id: c.id,
      name: [c.first_name, c.last_name].filter(Boolean).join(" "),
    }));
  } catch {
    return [];
  }
}

/** The client's contacts; an empty id (a meeting filed on nobody) is nobody's people. */
export function clientContacts(companyId: string): Promise<ClientContact[]> {
  if (!companyId) return Promise.resolve([]);
  const cached = cache.get(companyId);
  if (cached) return cached;
  const request = fetchContacts(companyId);
  cache.set(companyId, request);
  return request;
}

/** After an inline create: the next read sees the new person. */
export function forgetClientContacts(): void {
  cache.clear();
}
