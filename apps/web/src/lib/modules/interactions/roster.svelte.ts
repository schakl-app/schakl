/**
 * The contact roster an interaction form is editing (#300) — the state behind `ContactChips`.
 *
 * Every surface that logs or re-files a contactmoment needs the same four behaviours, and each
 * of the three had grown its own copy of them for a *single* contact:
 *
 * 1. **Options follow the moment's effective client.** The host's pinned client, the one picked
 *    in the form, or the one backfilled from a project/task pick — all re-scope the roster, so
 *    a moment filed to client B never offers client A's people.
 * 2. **Stored chips stay pickable.** A moment may legitimately name someone its client is not
 *    linked to; opening its edit form must not quietly drop them and rewrite history on save.
 * 3. **A client the user *changed* does drop the people the new client doesn't know** — the same
 *    cascade the task picker runs — and says so, rather than silently blanking.
 * 4. **An inline-created person is selected and remembered**, and the shared per-scope cache is
 *    dropped so the next form to open knows about them (#290).
 *
 * One copy, because three that drift is exactly how the single-contact picker ended up offering
 * the whole address book on the Interacties page while the panel's narrowed correctly.
 */
import { contactsForScope, forgetContacts, type ContactRow } from "./contacts";

export interface ContactOption {
  value: string;
  label: string;
  hint?: string;
  /** The client this person is attached to — the @-mention subtitle, not a filter. */
  company?: string;
}

/** The shape both the API's `contacts` roster and a prefill carry. */
export interface ContactRef {
  id: string;
  name?: string | null;
}

function toOption(row: ContactRow): ContactOption {
  return {
    value: row.id,
    label: `${row.first_name} ${row.last_name ?? ""}`.trim(),
    hint: row.email ?? undefined,
    company: row.companies?.[0]?.name,
  };
}

/**
 * What a form opens on: the row's stored roster when editing, else whatever single contact the
 * host pinned when creating.
 *
 * `contacts` is read first and `contact_id` only as a fallback, because they are the same fact
 * at two ages: a panel payload rendered by an older API build (or a cached page) carries only
 * the lead, and opening such a row on an empty roster would post one back and drop everybody
 * else on save.
 */
export function initialContacts(
  interaction: { contacts?: ContactRef[]; contact_id?: string | null; contact_name?: string | null } | null,
  prefill: Record<string, string | null | undefined> = {},
): ContactRef[] {
  if (interaction) {
    if (interaction.contacts?.length) return interaction.contacts;
    return interaction.contact_id
      ? [{ id: interaction.contact_id, name: interaction.contact_name }]
      : [];
  }
  return typeof prefill.contact_id === "string" && prefill.contact_id
    ? [{ id: prefill.contact_id }]
    : [];
}

export class ContactRoster {
  /** The picked ids, in chip order. The first is the lead the API mirrors (#300). */
  picked = $state<string[]>([]);
  /** Every option the picker may offer, including stored chips outside the current scope. */
  options = $state<ContactOption[]>([]);
  /** A client change dropped someone — the form says so instead of just losing a chip. */
  cleared = $state(false);

  /**
   * `null` until the first roster lands, and that distinction is the whole point of it:
   * dropping out-of-scope picks is only right when the *user* changed the client, never
   * merely because an edit form opened on a stored row.
   */
  #loadedScope: string | null = null;
  #scope = "";
  /** Labels for stored chips, so a person outside the fetched scope still reads as a name. */
  #known = new Map<string, string>();

  /** Seed from what is already on the row (an edit) or pinned by the host (a create). */
  constructor(initial: ContactRef[] = []) {
    this.picked = initial.map((c) => c.id);
    for (const c of initial) if (c.name) this.#known.set(c.id, c.name);
    this.options = initial
      .filter((c) => c.name)
      .map((c) => ({ value: c.id, label: c.name as string }));
  }

  /** The options still on offer — everyone not already wearing a chip. */
  get candidates(): ContactOption[] {
    return this.options.filter((o) => !this.picked.includes(o.value));
  }

  label(id: string): string {
    return this.options.find((o) => o.value === id)?.label ?? this.#known.get(id) ?? id;
  }

  /**
   * Re-scope to a client id (`""` = the whole org) and reload. Safe to call from an `$effect`
   * on every change: an in-flight answer for a scope that has since moved on is discarded, so
   * a fast click-through can't leave the picker showing the previous client's people.
   */
  async load(companyId: string): Promise<void> {
    this.#scope = companyId;
    const rows = await contactsForScope(companyId);
    if (this.#scope !== companyId) return;
    const options = rows.map(toOption);
    const inScope = new Set(rows.map((r) => r.id));
    const strays = this.picked.filter((id) => !inScope.has(id));
    if (strays.length && this.#loadedScope !== null) {
      // The user moved the moment to a client these people are not linked to.
      this.picked = this.picked.filter((id) => inScope.has(id));
      this.cleared = true;
    } else if (strays.length) {
      // Opening a stored row: keep them, labelled from what the row already told us.
      options.unshift(
        ...strays.map((id) => ({ value: id, label: this.#known.get(id) ?? id })),
      );
    } else if (this.picked.length) {
      this.cleared = false;
    }
    this.options = options;
    this.#loadedScope = companyId;
  }

  add(id: string): void {
    if (!id || this.picked.includes(id)) return;
    this.picked = [...this.picked, id];
    this.cleared = false;
  }

  remove(id: string): void {
    this.picked = this.picked.filter((p) => p !== id);
  }

  /** Promote a chip to lead — the one the API mirrors onto `contact_id` and sorts by. */
  lead(id: string): void {
    if (!this.picked.includes(id)) return;
    this.picked = [id, ...this.picked.filter((p) => p !== id)];
  }

  /** A person created from the picker's ＋: offer them, pick them, and drop the stale cache. */
  created(id: string, name: string): void {
    this.#known.set(id, name);
    if (!this.options.some((o) => o.value === id)) {
      this.options = [...this.options, { value: id, label: name }];
    }
    this.add(id);
    forgetContacts();
  }
}
