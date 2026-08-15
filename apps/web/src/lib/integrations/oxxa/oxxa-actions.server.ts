/**
 * The form actions behind the oxxa panel (issue #296). The domain detail page spreads these
 * into its `actions` — the same contract the Cloudflare and Drive panels use: a panel edits
 * through its host page, because SvelteKit actions live on the page.
 *
 * Keeping them here rather than inline in `routes/(app)/domains/[id]/+page.server.ts` is what
 * stops the domains route from growing registrar internals (CLAUDE.md §6): the host imports one
 * symbol and knows nothing about registers, nameserver groups or reseller credentials.
 *
 * Every action is named `oxxa*` so it cannot collide with the host page's own — the domains
 * module already owns a plain `refresh` (its DNS re-read), which is a different act entirely.
 */
import { fail, type RequestEvent } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import { MAX_NAMESERVERS, MIN_NAMESERVERS, parseNameservers } from "./types";

export const oxxaActions = {
  /**
   * The explicit "go look at the registrar" action. Its answer is returned to the page rather
   * than reloaded from `load`, because `load` deliberately reads only stored rows — a domain
   * page must not depend on OXXA being up (docs/PERFORMANCE.md).
   */
  oxxaRefresh: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const account_id = String(form.get("account_id") ?? "") || undefined;
    const { data, error } = await apiFor(event).POST("/api/v1/oxxa/domains/{domain_id}/refresh", {
      params: {
        path: { domain_id: event.params.id as string },
        // Omitted with one account: the API resolves the only active one itself, and only
        // refuses to pick when there are several (`errors.oxxa_account_ambiguous`).
        query: account_id ? { account_id } : {},
      },
    });
    if (error) return fail(400, { oxxaError: apiErrorKey(error).key });
    return { oxxaStatus: data };
  },

  /**
   * Repoint the domain's delegation at the registrar. Its own permission on the API side
   * (`oxxa.registrar.manage`), because this changes where the world resolves a client's domain.
   *
   * Idempotent: pushing the delegation a domain already has comes back `changed=false` and
   * writes nothing at OXXA, which is what makes a retry after a half-finished setup free.
   */
  oxxaPush: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const nameservers = parseNameservers(String(form.get("nameservers") ?? ""));
    // A pre-check, not the check: the API validates and normalises these again, and it is the
    // boundary. This exists so an obviously empty box answers instantly instead of round-tripping.
    if (nameservers.length < MIN_NAMESERVERS || nameservers.length > MAX_NAMESERVERS) {
      return fail(400, { oxxaError: "errors.invalid_nameserver_count" });
    }
    const { data, error } = await apiFor(event).POST(
      "/api/v1/oxxa/domains/{domain_id}/nameservers",
      {
        params: { path: { domain_id: event.params.id as string } },
        body: {
          nameservers,
          account_id: String(form.get("account_id") ?? "") || null,
        },
      },
    );
    if (error) return fail(400, { oxxaError: apiErrorKey(error).key });
    // A **refused** push still comes back 200, carrying `ok: false` and an i18n key in `error`.
    // That is deliberate on the API side: raising would have rolled back the row recording what
    // we asked for and why it failed (see `push_nameservers`' except-branch), and the panel would
    // reopen empty. So the result travels through unchanged and the panel reads `ok` — a 200 here
    // is "the API answered", not "the registrar agreed".
    return { oxxaPush: data };
  },
};
