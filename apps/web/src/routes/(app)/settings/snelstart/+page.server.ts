import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { checked } from "$lib/core/forms";
import type {
  SnelstartAccount,
  SnelstartCandidate,
  SnelstartLedger,
  SnelstartRun,
  SnelstartVerify,
} from "$lib/integrations/snelstart/types";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad, RequestEvent } from "./$types";

/**
 * Instellingen → SnelStart (epic #377, issue #31): the administration an agency's bookkeeping
 * actually lives in, and everything that travels to and from it.
 *
 * Org-wide configuration, so it lives here rather than as a button on an invoice (docs/UX.md
 * principle 6). Three things about the shape are decisions rather than habit.
 *
 * **Two permissions, and the load mirrors the split rather than restating it.** Holding the
 * credential (`snelstart.settings.manage`) and acting through it (`snelstart.sync.run`) are
 * different grants for a reason the module's own `permissions.py` argues: the person who
 * reconciles who has paid is not necessarily the person who may rotate a koppelsleutel. So a
 * sync-only caller reads the administrations through `/accounts/options`, which declares
 * `sync.run` and hands back the same shape, and sees no credential control at all. Asking
 * `/accounts` for them would spend a request to render a 403 as an empty screen.
 *
 * **The read and the act are different routes, and this load only ever reads.** `GET /accounts`,
 * `/ledgers` and `/runs` answer from stored rows and never call SnelStart, so the screen opens at
 * full speed and still renders when SnelStart is down. Verifying, syncing and pushing are the
 * explicit *go and look* actions, and they are buttons.
 *
 * **The relation review is opt-in** (`?review=<account id>`). `GET /relations` is the one read on
 * this screen that talks to SnelStart live — it fetches every relation in the administration — so
 * putting it on page load would make an ordinary settings visit wait on somebody else's server,
 * which is the exact failure the split above exists to prevent. It is a review pass somebody
 * *starts*, and the URL carries it so the state survives a reload and can be linked to.
 */

export const load: PageServerLoad = async (event) => {
  const mayManage = can(event.locals.user, "snelstart.settings.manage");
  const maySync = can(event.locals.user, "snelstart.sync.run");
  if (!mayManage && !maySync) throw redirect(303, "/settings");

  const mayWrite = can(event.locals.user, "snelstart.ledger.write");
  // Pushing an invoice declares **both** keys at the API (`snelstart.ledger.write` *and*
  // `invoicing.invoice.write`), so the control mirrors both (#310): gating it on this module's
  // key alone draws a button the API then refuses, and the 403 could not say which half was
  // missing.
  const mayPushInvoices = mayWrite && can(event.locals.user, "invoicing.invoice.write");
  // The provider catalog and the client list each read on their own permission. Fetching them
  // for a caller who holds neither would spend a request to render a 403 as an empty dropdown,
  // and the field would silently claim the tenant keeps no such list.
  const mayReadProviders = can(event.locals.user, "settings.providers.read");
  const mayReadCompanies = can(event.locals.user, "companies.company.read");

  const typed = apiFor(event);

  // Two paths, one shape. `/accounts` declares `settings.manage`; `/accounts/options` declares
  // the weaker `sync.run`, because choosing which administration to push into is the sync
  // caller's job and should not require holding the credential screen's permission. A holder of
  // only `sync.run` reaching for the first would get a card that always 403s (#253).
  const accountsRes = mayManage
    ? await typed.GET("/api/v1/snelstart/accounts")
    : await typed.GET("/api/v1/snelstart/accounts/options");
  const accounts: SnelstartAccount[] = accountsRes.data ?? [];

  // Which administration the run log and the review are about. The URL is the view: a reload
  // lands on the same one and the tab is shareable. Falling back to the first row rather than to
  // nothing, because the overwhelmingly common case is exactly one set of books.
  const selectedId =
    accounts.find((a) => a.id === event.url.searchParams.get("account"))?.id ??
    accounts[0]?.id ??
    null;
  // The one live read, and only when somebody asked for it.
  const reviewId = accounts.find((a) => a.id === event.url.searchParams.get("review"))?.id ?? null;

  const [providers, companies, runs, relations, ledgerLists] = await Promise.all([
    mayReadProviders
      ? // No `kind` filter: `providers.kind` knows email, dns, registrar and hosting, and an
        // accounting package is none of them. Rather than invent a fifth kind for a label, the
        // link is left free — it is the tenant's own bookkeeping, not something schakl reads.
        typed.GET("/api/v1/providers")
      : Promise.resolve(null),
    mayReadCompanies
      ? // The options behind the review's "pair this with" picker. `count: false` because a total
        // nobody draws is a second query for nothing.
        //
        // TODO(#377): 200 is the endpoint's own ceiling, so an agency past that number cannot
        // reach every client from this picker. The fix is `Combobox`'s `onsearch` seam against a
        // `+server.ts` proxy (the shape `marketing/companies/+server.ts` already uses), not a
        // bigger number — and it is worth doing before this screen meets an agency that size,
        // because a picker silently missing rows looks exactly like a client that is not there.
        typed.GET("/api/v1/companies", {
          params: { query: { limit: 200, offset: 0, count: false, sort: "name" } },
        })
      : Promise.resolve(null),
    maySync && selectedId
      ? typed.GET("/api/v1/snelstart/accounts/{account_id}/runs", {
          params: { path: { account_id: selectedId }, query: { limit: 20 } },
        })
      : Promise.resolve(null),
    maySync && reviewId
      ? typed.GET("/api/v1/snelstart/accounts/{account_id}/relations", {
          params: { path: { account_id: reviewId } },
        })
      : Promise.resolve(null),
    // One cached read per administration, and an agency has one or two: these are the revenue
    // accounts the per-account settings form picks from, and they have to be in hand before the
    // form is opened or the picker draws empty on first click. Keyed by account so the page
    // never has to guess which list belongs to which row.
    maySync
      ? Promise.all(
          accounts.map(async (account) => ({
            id: account.id,
            ledgers:
              (
                await typed.GET("/api/v1/snelstart/accounts/{account_id}/ledgers", {
                  params: { path: { account_id: account.id } },
                })
              ).data ?? [],
          })),
        )
      : Promise.resolve([]),
  ]);

  return {
    accounts,
    selectedId,
    reviewId,
    runs: runs?.data ?? [],
    relations: relations?.data ?? [],
    ledgers: Object.fromEntries(ledgerLists.map((row) => [row.id, row.ledgers])) as Record<
      string,
      SnelstartLedger[]
    >,
    providers: (providers?.data ?? []).map((p) => ({ id: p.id, name: p.name })),
    companies: (companies?.data?.items ?? []).map((c) => ({ id: c.id, name: c.name })),
    mayManage,
    maySync,
    mayWrite,
    mayPushInvoices,
    mayReadProviders,
    mayReadCompanies,
  };
};

/**
 * Every run action is the same three lines; only the path differs.
 *
 * The paths are a **literal union** rather than a template string, so the generated client still
 * checks them: a route renamed at the API breaks this file at build time, which is the whole
 * point of generating the client (CLAUDE.md §3).
 */
type RunPath =
  | "/api/v1/snelstart/accounts/{account_id}/sync/reference"
  | "/api/v1/snelstart/accounts/{account_id}/sync/relations"
  | "/api/v1/snelstart/accounts/{account_id}/sync/payments"
  | "/api/v1/snelstart/accounts/{account_id}/push/relations"
  | "/api/v1/snelstart/accounts/{account_id}/push/invoices"
  | "/api/v1/snelstart/accounts/{account_id}/push/articles";

function runAction(path: RunPath) {
  return async (event: RequestEvent) => {
    const form = await event.request.formData();
    const account_id = String(form.get("account_id") ?? "");
    if (!account_id) return fail(400, { error: "errors.required" });
    const { data, error } = await apiFor(event).POST(path, {
      params: { path: { account_id } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { run: (data ?? null) as SnelstartRun | null };
  };
}

export const actions: Actions = {
  create: async (event) => {
    const form = await event.request.formData();
    const client_key = String(form.get("client_key") ?? "").trim();
    const { data, error } = await apiFor(event).POST("/api/v1/snelstart/accounts", {
      body: {
        name: String(form.get("name") ?? "").trim(),
        // Empty means "open a pending row and let the activation flow fill it" — the whole
        // reason the API makes this optional, and the difference between the two connect paths.
        client_key: client_key || null,
        subscription_key: String(form.get("subscription_key") ?? "").trim() || null,
        provider_id: String(form.get("provider_id") ?? "") || null,
        active: true,
      },
    });
    if (error || !data) return fail(400, { error: apiErrorKey(error).key });
    const account: SnelstartAccount = data;
    // A pasted key is verified immediately: the API deliberately does not verify on create — a
    // typo must not read as a failed save — which is exactly why the screen that *can* report it
    // does so here, and a credential that works is not the answer anyway. Which administration
    // it opens is, and only a verify knows that.
    //
    // A pending row is **not** verified: there is no key yet, so the probe would answer
    // `not_connected` and read as a failure on a flow that is going exactly to plan.
    const verified = client_key
      ? await apiFor(event).POST("/api/v1/snelstart/accounts/{account_id}/verify", {
          params: { path: { account_id: account.id } },
        })
      : null;
    // `saved`, not `created`: the two facts are independent and the page renders both. A rejected
    // credential is still a stored credential, and reporting only the refusal would let an admin
    // believe the save failed too and type it all in again.
    return {
      saved: true,
      createdId: account.id,
      verify: (verified?.data ?? null) as SnelstartVerify | null,
    };
  },

  update: async (event) => {
    const form = await event.request.formData();
    const account_id = String(form.get("account_id") ?? "");
    if (!account_id) return fail(400, { error: "errors.required" });
    // An empty koppelsleutel means "keep the stored one": the API never plays it back, so there
    // is nothing to send unchanged, and it reads a blank as absent rather than as "clear it" —
    // an account with no key is disconnected, and that is what removing it is for.
    const client_key = String(form.get("client_key") ?? "").trim() || null;
    // The subscription key is the opposite, and it needs its own control to say so. Falling back
    // to the install's partner key is a real state, so an empty string *clears* — which a blank
    // text box cannot distinguish from "I did not touch this". The checkbox is the distinction.
    const dropSubscription = checked(form, "drop_subscription_key");
    const typedSubscription = String(form.get("subscription_key") ?? "").trim();
    const subscription_key = dropSubscription ? "" : typedSubscription || null;
    const { error } = await apiFor(event).PATCH("/api/v1/snelstart/accounts/{account_id}", {
      params: { path: { account_id } },
      body: {
        name: String(form.get("name") ?? "").trim() || null,
        client_key,
        subscription_key,
        provider_id: String(form.get("provider_id") ?? "") || null,
        active: checked(form, "active"),
        // Always sent, empty included: an empty string is how "no default account" is expressed,
        // and omitting it would make clearing the picker impossible.
        default_ledger_code: String(form.get("default_ledger_code") ?? "").trim(),
        // Presence, never a particular posted value (`core/forms.checked`, #305).
        auto_push_invoices: checked(form, "auto_push_invoices"),
        attach_invoice_pdf: checked(form, "attach_invoice_pdf"),
        pull_payments: checked(form, "pull_payments"),
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { saved: true };
  },

  verify: async (event) => {
    const form = await event.request.formData();
    const account_id = String(form.get("account_id") ?? "");
    if (!account_id) return fail(400, { error: "errors.required" });
    const { data, error } = await apiFor(event).POST(
      "/api/v1/snelstart/accounts/{account_id}/verify",
      { params: { path: { account_id } } },
    );
    // A rejected key is a `200` carrying `ok: false` (the probe succeeded; its answer was no), so
    // an `error` here means the request itself failed — an unreadable secret, a missing account.
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { verify: (data ?? null) as SnelstartVerify | null };
  },

  delete: async (event) => {
    const form = await event.request.formData();
    const account_id = String(form.get("account_id") ?? "");
    if (!account_id) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).DELETE("/api/v1/snelstart/accounts/{account_id}", {
      params: { path: { account_id } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { deleted: true };
  },

  // --- the seven acts, in the order the screen lists them ------------------------ //
  syncReference: runAction("/api/v1/snelstart/accounts/{account_id}/sync/reference"),
  syncRelations: runAction("/api/v1/snelstart/accounts/{account_id}/sync/relations"),
  pushRelations: runAction("/api/v1/snelstart/accounts/{account_id}/push/relations"),
  pushInvoices: runAction("/api/v1/snelstart/accounts/{account_id}/push/invoices"),
  syncPayments: runAction("/api/v1/snelstart/accounts/{account_id}/sync/payments"),
  pushArticles: runAction("/api/v1/snelstart/accounts/{account_id}/push/articles"),

  adopt: async (event) => {
    const form = await event.request.formData();
    const account_id = String(form.get("account_id") ?? "");
    const link_id = String(form.get("link_id") ?? "");
    const local_id = String(form.get("local_id") ?? "");
    if (!account_id || !link_id || !local_id) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).POST(
      "/api/v1/snelstart/accounts/{account_id}/links/{link_id}/adopt",
      { params: { path: { account_id, link_id } }, body: { local_id } },
    );
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { adopted: true };
  },
};
