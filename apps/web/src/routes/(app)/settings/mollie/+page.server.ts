import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

/**
 * Instellingen → Mollie (epic #269, issue #267): the payment credentials, and nothing else.
 *
 * The third "connect a credential" screen, after Cloudflare and OXXA, and it follows their
 * shape deliberately — org-wide configuration lives under Instellingen (docs/UX.md principle 6),
 * the whole screen is guarded once on `mollie.settings.manage` because every control on it
 * writes or reveals credential state, and the key itself is write-only: the API reports
 * `api_key_configured` and never plays the value back.
 *
 * What is different here is what the credential *does*. A wrong DNS token fails loudly at the
 * next sync; a wrong payment key fails at the moment a client tries to pay an invoice, which is
 * the worst possible time to find out. That is why this screen creates and then immediately
 * verifies, why it shows the mode Mollie's key says it is in, and why it prints the notification
 * URL: Mollie posts there when a payment changes, and behind an access proxy (docs/DEPLOY.md)
 * somebody has to allow that path. An admin who cannot see the URL cannot allow it, and
 * "collected but never booked" is a silent failure with no clue anywhere else in the product.
 */
export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "mollie.settings.manage")) throw redirect(303, "/settings");
  const api = apiFor(event);

  // The provider catalog reads on `settings.providers.read`, a different permission from this
  // screen's own, so it is fetched only for a caller who holds it — asking anyway would spend a
  // request to render a 403 as an empty dropdown, and the field would silently claim the tenant
  // keeps no provider list (the rule Instellingen → OXXA states about its register). The picker
  // is hidden in that case rather than offered empty (#253: a control that refuses is broken).
  const mayReadProviders = can(event.locals.user, "settings.providers.read");

  const [accounts, providers] = await Promise.all([
    api.GET("/api/v1/mollie/accounts"),
    // No `kind` filter: `providers.kind` knows email, dns, registrar and hosting, and a payment
    // provider is none of them. Rather than invent a fifth kind for a label, the link is left
    // free — it is an optional bit of the tenant's own bookkeeping, not something schakl reads.
    mayReadProviders ? api.GET("/api/v1/providers") : Promise.resolve(null),
  ]);

  return {
    accounts: accounts.data ?? [],
    providers: (providers?.data ?? []).map((p) => ({ id: p.id, name: p.name })),
    mayReadProviders,
  };
};

export const actions: Actions = {
  create: async (event) => {
    const form = await event.request.formData();
    const { data, error } = await apiFor(event).POST("/api/v1/mollie/accounts", {
      body: {
        name: String(form.get("name") ?? "").trim(),
        api_key: String(form.get("api_key") ?? "").trim(),
        provider_id: String(form.get("provider_id") ?? "") || null,
        active: true,
      },
    });
    if (error || !data) return fail(400, { error: apiErrorKey(error).key });
    // Verify immediately. The API deliberately does not verify on create — a typo must not read
    // as a failed save — which is exactly why the screen that *can* report it does it here. For
    // a payment key the answer is worth two things at once: whether Mollie accepts the
    // credential at all, and which methods the profile behind it actually offers, which is the
    // only place iDEAL-is-not-switched-on is visible before a client meets it at checkout.
    const verified = await apiFor(event).POST("/api/v1/mollie/accounts/{account_id}/verify", {
      params: { path: { account_id: data.id } },
    });
    // `saved`, not `created`: the two facts are independent and the page renders both. A rejected
    // credential is still a stored credential, and reporting only "Mollie weigert de sleutel"
    // would let an admin believe the save failed too and enter it all again.
    return { saved: true, verify: verified.data ?? null };
  },

  update: async (event) => {
    const form = await event.request.formData();
    const account_id = String(form.get("account_id") ?? "");
    if (!account_id) return fail(400, { error: "errors.required" });
    // An empty key means "keep the stored one" — the API never returns it, so there is nothing
    // to send back unchanged, and it reads a blank string as absent rather than as "clear it": a
    // payment credential is removed by deleting the account, never by blanking a field. The
    // provider link reads `null` the same way, so leaving the picker at "—" keeps what is there.
    const api_key = String(form.get("api_key") ?? "").trim() || null;
    const { error } = await apiFor(event).PATCH("/api/v1/mollie/accounts/{account_id}", {
      params: { path: { account_id } },
      body: {
        name: String(form.get("name") ?? "").trim() || null,
        api_key,
        provider_id: String(form.get("provider_id") ?? "") || null,
        active: form.get("active") !== null,
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
      "/api/v1/mollie/accounts/{account_id}/verify",
      { params: { path: { account_id } } },
    );
    // A rejected key is a `200` carrying `ok: false` (the probe succeeded; its answer was no), so
    // an `error` here means the request itself failed — an unreadable secret, a missing account.
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { verify: data ?? null };
  },

  delete: async (event) => {
    const form = await event.request.formData();
    const account_id = String(form.get("account_id") ?? "");
    if (!account_id) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).DELETE("/api/v1/mollie/accounts/{account_id}", {
      params: { path: { account_id } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { deleted: true };
  },
};
