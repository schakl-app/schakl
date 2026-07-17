import { error as httpError, fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad, RequestEvent } from "./$types";

// Console org detail (epic #199). Tenant data (members, settings) only renders after this
// instance owner claimed the org's service PIN — until then the API answers 403
// errors.service_pin_required and this page shows the unlock form instead.
export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  const [detail, access, modules] = await Promise.all([
    api.GET("/api/v1/instance/orgs/{org_id}", {
      params: { path: { org_id: event.params.id } },
    }),
    api.GET("/api/v1/instance/orgs/{org_id}/service-access", {
      params: { path: { org_id: event.params.id } },
    }),
    api.GET("/api/v1/meta/modules"),
  ]);

  if (detail.data) {
    return {
      locked: false as const,
      org: detail.data,
      summary: null,
      access: access.data ?? null,
      availableModules: modules.data?.enabled_modules ?? [],
      baseDomain: modules.data?.base_domain ?? "",
    };
  }
  if (apiErrorKey(detail.error).key !== "errors.service_pin_required") throw httpError(404);
  // Locked: fall back to the PIN-free summary (slug, status, plan) from the org list.
  const orgs = await api.GET("/api/v1/instance/orgs");
  const summary = (orgs.data ?? []).find((org) => org.id === event.params.id);
  if (!summary) throw httpError(404);
  return {
    locked: true as const,
    org: null,
    summary,
    access: access.data ?? null,
    availableModules: modules.data?.enabled_modules ?? [],
    baseDomain: modules.data?.base_domain ?? "",
  };
};

function orgPath(event: RequestEvent) {
  return { params: { path: { org_id: event.params.id } } };
}

const transition = (path: "suspend" | "activate") => async (event: RequestEvent) => {
  const { error } = await apiFor(event).POST(
    `/api/v1/instance/orgs/{org_id}/${path}`,
    orgPath(event),
  );
  if (error) return fail(400, { error: apiErrorKey(error).key });
  return { changed: true };
};

export const actions: Actions = {
  unlock: async (event) => {
    const form = await event.request.formData();
    const pin = String(form.get("pin") ?? "").trim();
    if (!pin) return fail(400, { error: "errors.required", unlockError: true });
    const { error } = await apiFor(event).POST(
      "/api/v1/instance/orgs/{org_id}/service-access",
      { ...orgPath(event), body: { pin } },
    );
    if (error) return fail(400, { error: apiErrorKey(error).key, unlockError: true });
    return { unlocked: true };
  },

  plan: async (event) => {
    const form = await event.request.formData();
    const plan = String(form.get("plan") ?? "");
    const trialDays = Number(form.get("trial_days") ?? "") || null;
    const { error } = await apiFor(event).PATCH("/api/v1/instance/orgs/{org_id}/plan", {
      ...orgPath(event),
      body: { plan, trial_days: plan === "trial" ? trialDays : null },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { planSaved: true };
  },

  update: async (event) => {
    const form = await event.request.formData();
    const name = String(form.get("name") ?? "").trim();
    const slug = String(form.get("slug") ?? "")
      .trim()
      .toLowerCase();
    if (!name || !slug) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).PATCH("/api/v1/instance/orgs/{org_id}", {
      ...orgPath(event),
      body: { name, slug },
    });
    if (error) {
      const parsed = apiErrorKey(error);
      return fail(400, { error: parsed.fields?.slug ?? parsed.key });
    }
    return { updated: true };
  },

  suspend: transition("suspend"),
  activate: transition("activate"),

  softDelete: async (event) => {
    const { error } = await apiFor(event).DELETE("/api/v1/instance/orgs/{org_id}", orgPath(event));
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { changed: true };
  },

  purge: async (event) => {
    const form = await event.request.formData();
    const confirm = String(form.get("confirm") ?? "").trim();
    const { error } = await apiFor(event).POST("/api/v1/instance/orgs/{org_id}/purge", {
      ...orgPath(event),
      body: { confirm },
    });
    if (error) {
      const parsed = apiErrorKey(error);
      return fail(400, { error: parsed.fields?.confirm ?? parsed.key, purgeError: true });
    }
    throw redirect(303, "/console");
  },

  modules: async (event) => {
    const form = await event.request.formData();
    const modules = form.getAll("modules").map(String);
    const { error } = await apiFor(event).PATCH("/api/v1/instance/orgs/{org_id}/modules", {
      ...orgPath(event),
      body: { enabled_modules: modules },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { updated: true };
  },

  impersonate: async (event) => {
    const form = await event.request.formData();
    const userId = String(form.get("user_id") ?? "");
    const api = apiFor(event);
    const { data, error } = await api.POST("/api/v1/instance/orgs/{org_id}/impersonate", {
      ...orgPath(event),
      body: { user_id: userId, minutes: 30 },
    });
    if (error || !data) return fail(400, { error: apiErrorKey(error).key });

    // The console never runs on the org's own host: hand the grant to that host's
    // /impersonate route, which stores the cookie there and lands on the dashboard.
    const { data: org } = await api.GET("/api/v1/instance/orgs/{org_id}", orgPath(event));
    const { data: meta } = await api.GET("/api/v1/meta/modules");
    const targetHost = org?.custom_domain ?? `${org?.slug}.${meta?.base_domain ?? ""}`;
    const port = event.url.port ? `:${event.url.port}` : "";
    throw redirect(
      303,
      `${event.url.protocol}//${targetHost}${port}/impersonate?token=${encodeURIComponent(data.token)}`,
    );
  },
};
