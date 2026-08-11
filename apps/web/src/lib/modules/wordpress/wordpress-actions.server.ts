/**
 * The form actions behind the WordPress panel (docs/WORDPRESS.md). The website detail page
 * spreads these into its `actions` — the same contract the Cloudflare and uptime panels use: a
 * panel edits through its host page, because SvelteKit actions live on the page.
 *
 * Keeping them here rather than inline in `routes/(app)/websites/[id]/+page.server.ts` is what
 * stops the websites route from growing WordPress internals (CLAUDE.md §6): the host imports one
 * symbol and knows nothing about application passwords, capability probes or Rank Math.
 *
 * Every action is named `wp*` so it cannot collide with the host page's own.
 */
import { fail, type RequestEvent } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { checked } from "$lib/core/forms";
import { apiFor } from "$lib/core/session";

/** The site row for this website again, after a write. `null` if the re-read fails. */
async function reload(event: RequestEvent) {
  const { data } = await apiFor(event).GET(
    "/api/v1/wordpress/sites/by-website/{website_id}",
    { params: { path: { website_id: event.params.id as string } } },
  );
  return data ?? null;
}

export const wordpressActions = {
  /** Connect this website's WordPress, then immediately verify it. */
  wpConnect: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const base_url = String(form.get("base_url") ?? "").trim();
    const username = String(form.get("username") ?? "").trim();
    const app_password = String(form.get("app_password") ?? "").trim();
    if (!base_url || !username || !app_password) return fail(400, { wpError: "errors.required" });

    const api = apiFor(event);
    const { data, error } = await api.POST("/api/v1/wordpress/sites", {
      body: {
        website_id: event.params.id as string,
        base_url,
        username,
        app_password,
        active: true,
      },
    });
    if (error || !data) return fail(400, { wpError: apiErrorKey(error).key });

    // Verify straight away rather than leaving the row `pending`: somebody who has just pasted
    // a password wants to know it works, and the failure they most need to see — a host that
    // strips the `Authorization` header — is invisible until something asks.
    const { data: verified } = await api.POST("/api/v1/wordpress/sites/{site_id}/verify", {
      params: { path: { site_id: data.id } },
    });
    return { wpSite: await reload(event), wpVerify: verified ?? null };
  },

  /** Rotate the application password, or edit the URL/username. */
  wpUpdate: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const site_id = String(form.get("site_id") ?? "");
    if (!site_id) return fail(400, { wpError: "errors.required" });

    const app_password = String(form.get("app_password") ?? "").trim();
    const { error } = await apiFor(event).PATCH("/api/v1/wordpress/sites/{site_id}", {
      params: { path: { site_id } },
      body: {
        base_url: String(form.get("base_url") ?? "").trim() || undefined,
        username: String(form.get("username") ?? "").trim() || undefined,
        // Empty means "keep the stored password", never "clear it" — an empty field is how a
        // form says *I did not type here*, and disconnecting is a different button.
        app_password: app_password || undefined,
        // Presence, never a value: `FormCheckbox` posts "true" and a bare input posts "on", so
        // any read that names a particular string is a bug waiting for somebody to change the
        // control (`$lib/core/forms.checked`).
        active: checked(form, "active"),
      },
    });
    if (error) return fail(400, { wpError: apiErrorKey(error).key });
    return { wpSite: await reload(event) };
  },

  /**
   * The explicit "go look at the site" action. Its answer is returned to the page rather than
   * reloaded from `load`, because `load` reads only the stored row — a website page must not
   * depend on a client's WordPress being up (docs/PERFORMANCE.md).
   */
  wpVerify: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const site_id = String(form.get("site_id") ?? "");
    if (!site_id) return fail(400, { wpError: "errors.required" });
    const { data, error } = await apiFor(event).POST(
      "/api/v1/wordpress/sites/{site_id}/verify",
      { params: { path: { site_id } } },
    );
    if (error) return fail(400, { wpError: apiErrorKey(error).key });
    return { wpSite: await reload(event), wpVerify: data ?? null };
  },

  /**
   * Forget the credential here. It does **not** revoke the application password on the client's
   * site, which we could do: revoking is their act on their own profile screen, and doing it as
   * a side effect of tidying a list would break whatever else that password was minted for.
   */
  wpDisconnect: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const site_id = String(form.get("site_id") ?? "");
    if (!site_id) return fail(400, { wpError: "errors.required" });
    const { error } = await apiFor(event).DELETE("/api/v1/wordpress/sites/{site_id}", {
      params: { path: { site_id } },
    });
    if (error) return fail(400, { wpError: apiErrorKey(error).key });
    return { wpSite: null, wpDisconnected: true };
  },
};
