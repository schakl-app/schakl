import { error as httpError, fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { IMPERSONATION_COOKIE } from "$lib/core/impersonation";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad, RequestEvent } from "./$types";

export const load: PageServerLoad = async (event) => {
  if (!event.locals.user?.isInstanceAdmin) throw redirect(303, "/");
  const api = apiFor(event);
  const [org, modules, domain] = await Promise.all([
    api.GET("/api/v1/instance/orgs/{org_id}", {
      params: { path: { org_id: event.params.id } },
    }),
    api.GET("/api/v1/meta/modules"),
    api.GET("/api/v1/instance/orgs/{org_id}/domain", {
      params: { path: { org_id: event.params.id } },
    }),
  ]);
  if (!org.data) throw httpError(404);
  return {
    org: org.data,
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
    throw redirect(303, "/instance");
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
    if (error || !data) return fail(400, { error: apiErrorKey(error).key });

    // The API decides which of the two shapes applies, because it is the side that knows how a
    // hostname resolves to an org (#288). Same host — a box administering its own org — and the
    // grant comes straight back to be set beside the session that is already here. Another host
    // and there is only a single-use ticket to present there.
    if (data.token) {
      event.cookies.set(IMPERSONATION_COOKIE, data.token, {
        path: "/",
        httpOnly: true,
        sameSite: "lax",
        secure: event.url.protocol === "https:",
        maxAge: 60 * 60,
      });
      throw redirect(303, "/");
    }
    if (!data.handoff) return fail(400, { error: "errors.server" });
    // Returned, not redirected to: `form-action 'self'` (our own CSP) blocks a form submission
    // whose redirect chain leaves this origin, so the page navigates itself (#288).
    const port = event.url.port ? `:${event.url.port}` : "";
    return {
      handoffUrl:
        `${event.url.protocol}//${data.handoff.host}${port}` +
        `/impersonate?ticket=${encodeURIComponent(data.handoff.ticket)}`,
    };
  },
};
