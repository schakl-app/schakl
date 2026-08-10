import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

/**
 * Instellingen → Cloudflare (epic #278): the API tokens and the zone inventory they pull.
 *
 * Org-wide configuration, so it lives here rather than as a button on a domain (docs/UX.md
 * principle 6). Guarded on `cloudflare.settings.manage` — every control on this screen writes
 * or reveals credential state, so the gate belongs on the screen, not on each button (the
 * lesson from `/tasks/templates`).
 *
 * The token is write-only: the API reports `token_configured` and never plays the value back,
 * mirroring the Google client secret and the Ads developer token.
 */
export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "cloudflare.settings.manage")) throw redirect(303, "/settings");
  const api = apiFor(event);
  // The Pages list is inventory, not credential state, so it carries `cloudflare.dns.read` —
  // a different key from this screen's own gate. They are separable, so a role holding only
  // `settings.manage` must not fire a call that can do nothing but 403.
  const mayRead = can(event.locals.user, "cloudflare.dns.read");
  const [accounts, zones, providers, projects] = await Promise.all([
    api.GET("/api/v1/cloudflare/accounts"),
    // The zone list is the inventory, not a paged screen of its own: an agency's Cloudflare
    // accounts hold tens of zones, and the page shows them grouped by account.
    api.GET("/api/v1/cloudflare/zones", { params: { query: { limit: 200, offset: 0 } } }),
    // For the "which provider is this" label (#89). `dns` is the kind a Cloudflare account is;
    // the catalog endpoint filters server-side, so nothing unrelated crosses the wire.
    api.GET("/api/v1/providers", { params: { query: { kind: "dns" } } }),
    // The other half of what a sync pulls in. Without it, Pages appeared on this screen only as
    // three numbers on a banner that the next navigation throws away — so "did the sync find my
    // projects?" was answerable nowhere. The rows existed, and the one screen named after the
    // account they came from never showed them.
    mayRead
      ? api.GET("/api/v1/cloudflare/pages/projects")
      : Promise.resolve({ data: [] as never[] }),
  ]);
  return {
    accounts: accounts.data ?? [],
    zones: zones.data?.items ?? [],
    providers: (providers.data ?? []).map((p) => ({ id: p.id, name: p.name })),
    projects: projects.data ?? [],
  };
};

export const actions: Actions = {
  create: async (event) => {
    const form = await event.request.formData();
    const { data, error } = await apiFor(event).POST("/api/v1/cloudflare/accounts", {
      body: {
        name: String(form.get("name") ?? "").trim(),
        api_token: String(form.get("api_token") ?? "").trim(),
        cf_account_id: String(form.get("cf_account_id") ?? "").trim() || null,
        provider_id: String(form.get("provider_id") ?? "") || null,
        active: true,
      },
    });
    if (error || !data) return fail(400, { error: apiErrorKey(error).key });
    // Verify immediately: an admin who just pasted a token wants to know it works, and the
    // capability answer is what tells them which scope is missing if it doesn't.
    const verified = await apiFor(event).POST("/api/v1/cloudflare/accounts/{account_id}/verify", {
      params: { path: { account_id: data.id } },
    });
    return { created: true, verify: verified.data ?? null };
  },

  update: async (event) => {
    const form = await event.request.formData();
    const account_id = String(form.get("account_id") ?? "");
    if (!account_id) return fail(400, { error: "errors.required" });
    // An empty token means "keep the stored one" — the API never returns it, so there is
    // nothing to send back unchanged.
    const api_token = String(form.get("api_token") ?? "").trim() || null;
    const { error } = await apiFor(event).PATCH("/api/v1/cloudflare/accounts/{account_id}", {
      params: { path: { account_id } },
      body: {
        name: String(form.get("name") ?? "").trim() || null,
        api_token,
        cf_account_id: String(form.get("cf_account_id") ?? "").trim() || null,
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
      "/api/v1/cloudflare/accounts/{account_id}/verify",
      { params: { path: { account_id } } },
    );
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { verify: data ?? null };
  },

  sync: async (event) => {
    const form = await event.request.formData();
    const account_id = String(form.get("account_id") ?? "");
    if (!account_id) return fail(400, { error: "errors.required" });
    const { data, error } = await apiFor(event).POST(
      "/api/v1/cloudflare/accounts/{account_id}/sync",
      { params: { path: { account_id } } },
    );
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { sync: data ?? null };
  },

  delete: async (event) => {
    const form = await event.request.formData();
    const account_id = String(form.get("account_id") ?? "");
    if (!account_id) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).DELETE("/api/v1/cloudflare/accounts/{account_id}", {
      params: { path: { account_id } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { deleted: true };
  },

  unlinkZone: async (event) => {
    const form = await event.request.formData();
    const zone_id = String(form.get("zone_id") ?? "");
    if (!zone_id) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).DELETE("/api/v1/cloudflare/zones/{zone_id}/link", {
      params: { path: { zone_id } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { zoneUnlinked: true };
  },
};
