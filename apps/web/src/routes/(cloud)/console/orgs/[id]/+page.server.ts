import { error as httpError, fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad, RequestEvent } from "./$types";

// Console org detail (epic #199). Tenant data (members, settings) only renders after this
// instance owner claimed the org's service PIN — until then the API answers 403
// errors.service_pin_required and this page shows the unlock form instead.
export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  const [detail, access, modules, domain] = await Promise.all([
    api.GET("/api/v1/instance/orgs/{org_id}", {
      params: { path: { org_id: event.params.id } },
    }),
    api.GET("/api/v1/instance/orgs/{org_id}/service-access", {
      params: { path: { org_id: event.params.id } },
    }),
    api.GET("/api/v1/meta/modules"),
    // Routing state is platform data (#292): PIN-free like the org list.
    api.GET("/api/v1/instance/orgs/{org_id}/domain", {
      params: { path: { org_id: event.params.id } },
    }),
  ]);

  if (detail.data) {
    return {
      locked: false as const,
      org: detail.data,
      summary: null,
      access: access.data ?? null,
      availableModules: modules.data?.enabled_modules ?? [],
      baseDomain: modules.data?.base_domain ?? "",
      domain: domain.data ?? null,
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
    domain: domain.data ?? null,
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
    const { error } = await apiFor(event).POST("/api/v1/instance/orgs/{org_id}/service-access", {
      ...orgPath(event),
      body: { pin },
    });
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

  // End date (#199). An empty date means unlimited and switches the whole mechanism off for
  // this org — which is why it is sent as an explicit null rather than simply omitted.
  lifecycle: async (event) => {
    const form = await event.request.formData();
    const endsOn = String(form.get("ends_on") ?? "").trim();
    const asDays = (field: string) => {
      const raw = String(form.get(field) ?? "").trim();
      return raw === "" ? null : Number(raw);
    };
    const { error } = await apiFor(event).PATCH("/api/v1/instance/orgs/{org_id}/lifecycle", {
      ...orgPath(event),
      body: {
        // A date input yields a wall-clock day; the org ends at the end of it.
        ends_at: endsOn ? new Date(`${endsOn}T23:59:59Z`).toISOString() : null,
        grace_days: asDays("grace_days"),
        retention_days: asDays("retention_days"),
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { lifecycleSaved: true };
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

  // Operator-side custom domain (#292): activate asserts ownership (audited); claim only
  // reserves it and issues the TXT challenge for the org's own admin to complete.
  setDomain: async (event) => {
    const form = await event.request.formData();
    const domain = String(form.get("domain") ?? "")
      .trim()
      .toLowerCase();
    const mode = String(form.get("mode") ?? "activate");
    if (!domain) return fail(400, { error: "errors.required", domainError: true });
    const { error } = await apiFor(event).PUT("/api/v1/instance/orgs/{org_id}/domain", {
      ...orgPath(event),
      body: { domain, mode },
    });
    if (error) {
      const parsed = apiErrorKey(error);
      return fail(400, { error: parsed.fields?.domain ?? parsed.key, domainError: true });
    }
    return { domainSaved: true };
  },

  clearDomain: async (event) => {
    const { error } = await apiFor(event).DELETE(
      "/api/v1/instance/orgs/{org_id}/domain",
      orgPath(event),
    );
    if (error) return fail(400, { error: apiErrorKey(error).key, domainError: true });
    return { domainSaved: true };
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
    const { data, error } = await apiFor(event).POST("/api/v1/instance/orgs/{org_id}/impersonate", {
      ...orgPath(event),
      body: { user_id: userId, minutes: 30 },
    });
    if (error || !data?.handoff) return fail(400, { error: apiErrorKey(error).key });

    // The console never runs on the org's own host, and cookies are host-scoped — including on
    // a customer-owned custom domain, where there is no shared parent to widen one to. So the
    // API hands out a single-use **ticket** and the address to present it at (#288); that host's
    // /impersonate route exchanges it for the session + grant cookies. The ticket is the only
    // thing in this URL and it authenticates nothing by itself. The address is the API's own
    // canonical choice (#291) — live-aware, so a broken custom domain sends the operator to the
    // recovery host instead of a TLS error — which is why this no longer re-derives it here.
    //
    // Returned rather than redirected to, deliberately: our own CSP sends `form-action 'self'`
    // (audit F14) and Chrome applies it to the *whole redirect chain* of a form submission, so a
    // 303 out of this origin is blocked before the browser ever asks for it. The page navigates
    // itself instead — see the form in `+page.svelte`.
    const port = event.url.port ? `:${event.url.port}` : "";
    return {
      handoffUrl:
        `${event.url.protocol}//${data.handoff.host}${port}` +
        `/impersonate?ticket=${encodeURIComponent(data.handoff.ticket)}`,
    };
  },
};
