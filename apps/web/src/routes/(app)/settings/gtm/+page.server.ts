import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { checked } from "$lib/core/forms";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

// Instellingen → Tag Manager: the container links, the workspace schakl writes in, and the
// instance-wide off switch. Admin-only. There is no credential on this screen — Tag Manager
// rides the per-user Google grant — which is why the connection state is shown rather than a
// field: the fix for "no access" is a reconnect, not a password.
export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "google_tag_manager.settings.manage"))
    throw redirect(303, "/settings");
  const api = apiFor(event);
  const [settings, containers, companies, connection] = await Promise.all([
    api.GET("/api/v1/gtm/settings"),
    api.GET("/api/v1/gtm/containers"),
    api.GET("/api/v1/companies", {
      params: { query: { limit: 200, offset: 0, count: false, sort: "name" } },
    }),
    // Whether *this* person's Google account carries the Tag Manager scopes. The link below is
    // the only fix for a refusal, so the screen has to be able to say it is needed.
    api.GET("/api/v1/google/connections/me"),
  ]);
  const scopes = connection.data?.connection?.scopes ?? [];
  return {
    settings: settings.data ?? null,
    containers: containers.data ?? [],
    companies: companies.data?.items ?? [],
    // Any `tagmanager.*` scope means the grant reaches GTM at all; the API is the authority on
    // which of the four a given call needs, and says so when one is missing.
    connected: scopes.some((scope) =>
      scope.startsWith("https://www.googleapis.com/auth/tagmanager"),
    ),
  };
};

export const actions: Actions = {
  save: async (event) => {
    const form = await event.request.formData();
    const { error } = await apiFor(event).PUT("/api/v1/gtm/settings", {
      body: {
        // A checkbox posts its *value*, and an unticked one posts nothing. `checked()` asks
        // about presence, which is the only way of reading it that survives someone changing
        // the control (CLAUDE.md §10).
        writes_enabled: checked(form, "writes_enabled"),
        own_workspace: checked(form, "own_workspace"),
        workspace_name: String(form.get("workspace_name") ?? "").trim(),
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { saved: true };
  },

  link: async (event) => {
    const form = await event.request.formData();
    const { error } = await apiFor(event).POST("/api/v1/gtm/containers", {
      body: {
        public_id: String(form.get("public_id") ?? "").trim(),
        company_id: String(form.get("company_id") ?? "").trim() || null,
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { linked: true };
  },

  verify: async (event) => {
    const form = await event.request.formData();
    const { error } = await apiFor(event).POST("/api/v1/gtm/containers/{container_id}/verify", {
      params: { path: { container_id: String(form.get("container_id") ?? "") } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    // The outcome *is* the row: verify records what Google said either way and never raises,
    // so the reloaded container carries the answer.
    return { verified: true };
  },

  unlink: async (event) => {
    const form = await event.request.formData();
    const { error } = await apiFor(event).DELETE("/api/v1/gtm/containers/{container_id}", {
      params: { path: { container_id: String(form.get("container_id") ?? "") } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { unlinked: true };
  },
};
