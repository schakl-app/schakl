import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { resolvePaging } from "$lib/core/table/paging";

import type { Actions, PageServerLoad } from "./$types";

/**
 * Instellingen → OXXA (issue #296): the reseller logins and the register they pull.
 *
 * Org-wide configuration, so it lives here rather than as a button on a domain (docs/UX.md
 * principle 6). Guarded on `oxxa.settings.manage` — every control on this screen writes or
 * reveals credential state, so the gate belongs on the screen, not on each button.
 *
 * The API password is write-only: the API reports `password_configured` and never plays the
 * value back, mirroring the Cloudflare API token and the Google client secret.
 *
 * The register itself reads on a **different** permission (`oxxa.registrar.sync`), so it is
 * fetched only for a caller who holds it. Asking anyway would spend a request to render a 403
 * as an empty table, and the screen would silently claim the register is empty.
 */
export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "oxxa.settings.manage")) throw redirect(303, "/settings");
  const api = apiFor(event);

  const q = event.url.searchParams.get("q")?.trim() || null;
  const unmatched = event.url.searchParams.get("unmatched") === "1";
  const mayRead = can(event.locals.user, "oxxa.registrar.sync");
  // A reseller register runs to thousands of names, so the search is *a* way to find one and the
  // pager is the other. It carries no saved size — this screen keeps no column layout to hang one
  // on — so `?size=` lasts the visit (`paging.ts`).
  const paging = resolvePaging(event.url);

  const [accounts, providers, register] = await Promise.all([
    api.GET("/api/v1/oxxa/accounts"),
    // For the "which provider is this" label (#89). `registrar` is the kind an OXXA login is;
    // the catalog endpoint filters server-side, so nothing unrelated crosses the wire.
    api.GET("/api/v1/providers", { params: { query: { kind: "registrar" } } }),
    mayRead
      ? api.GET("/api/v1/oxxa/domains", {
          params: {
            query: {
              limit: paging.limit,
              offset: paging.offset,
              // `linked=false` is the one worth looking at: domains the agency renews that no
              // schakl record — and therefore no invoice — knows about.
              ...(unmatched ? { linked: false } : {}),
              ...(q ? { q } : {}),
            },
          },
        })
      : Promise.resolve(null),
  ]);

  return {
    accounts: accounts.data ?? [],
    providers: (providers.data ?? []).map((p) => ({ id: p.id, name: p.name })),
    register: register?.data?.items ?? [],
    registerTotal: register?.data?.total ?? 0,
    paging,
    mayReadRegister: mayRead,
    q: q ?? "",
    unmatched,
  };
};

export const actions: Actions = {
  create: async (event) => {
    const form = await event.request.formData();
    const { data, error } = await apiFor(event).POST("/api/v1/oxxa/accounts", {
      body: {
        name: String(form.get("name") ?? "").trim(),
        api_user: String(form.get("api_user") ?? "").trim(),
        api_password: String(form.get("api_password") ?? "").trim(),
        provider_id: String(form.get("provider_id") ?? "") || null,
        active: true,
      },
    });
    if (error || !data) return fail(400, { error: apiErrorKey(error).key });
    // Verify immediately. The API deliberately does not verify on create — a typo must not read
    // as a failed save — which is exactly why the screen that *can* report it does it here: an
    // admin who just pasted a credential wants to know whether it works, and how much credit
    // the register still holds.
    const verified = await apiFor(event).POST("/api/v1/oxxa/accounts/{account_id}/verify", {
      params: { path: { account_id: data.id } },
    });
    // `saved`, not `created`: the two facts are independent and the page renders both. A rejected
    // credential is still a stored credential, and reporting only "Verificatie mislukt" would let
    // an admin believe the save failed too and enter it all again.
    return { saved: true, verify: verified.data ?? null };
  },

  update: async (event) => {
    const form = await event.request.formData();
    const account_id = String(form.get("account_id") ?? "");
    if (!account_id) return fail(400, { error: "errors.required" });
    // An empty password means "keep the stored one" — the API never returns it, so there is
    // nothing to send back unchanged, and it refuses an empty string outright.
    const api_password = String(form.get("api_password") ?? "").trim() || null;
    const { error } = await apiFor(event).PATCH("/api/v1/oxxa/accounts/{account_id}", {
      params: { path: { account_id } },
      body: {
        name: String(form.get("name") ?? "").trim() || null,
        api_user: String(form.get("api_user") ?? "").trim() || null,
        api_password,
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
    const { data, error } = await apiFor(event).POST("/api/v1/oxxa/accounts/{account_id}/verify", {
      params: { path: { account_id } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { verify: data ?? null };
  },

  sync: async (event) => {
    const form = await event.request.formData();
    const account_id = String(form.get("account_id") ?? "");
    if (!account_id) return fail(400, { error: "errors.required" });
    const { data, error } = await apiFor(event).POST("/api/v1/oxxa/accounts/{account_id}/sync", {
      params: { path: { account_id } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { sync: data ?? null };
  },

  delete: async (event) => {
    const form = await event.request.formData();
    const account_id = String(form.get("account_id") ?? "");
    if (!account_id) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).DELETE("/api/v1/oxxa/accounts/{account_id}", {
      params: { path: { account_id } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { deleted: true };
  },
};
