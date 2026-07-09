import { error as httpError, fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { IMPERSONATION_COOKIE } from "$lib/core/impersonation";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad, RequestEvent } from "./$types";

export const load: PageServerLoad = async (event) => {
  if (!event.locals.user?.isInstanceAdmin) throw redirect(303, "/");
  const api = apiFor(event);
  const [org, modules] = await Promise.all([
    api.GET("/api/v1/instance/orgs/{org_id}", {
      params: { path: { org_id: event.params.id } },
    }),
    api.GET("/api/v1/meta/modules"),
  ]);
  if (!org.data) throw httpError(404);
  return {
    org: org.data,
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
    const api = apiFor(event);
    const { data, error } = await api.POST("/api/v1/instance/orgs/{org_id}/impersonate", {
      ...orgPath(event),
      body: { user_id: userId, minutes: 30 },
    });
    if (error || !data) return fail(400, { error: apiErrorKey(error).key });

    // The grant only acts on the target org's own hostname. Same host → store the cookie
    // and go home; different host → hand the token to that host's /impersonate route.
    const { data: org } = await api.GET("/api/v1/instance/orgs/{org_id}", orgPath(event));
    const { data: meta } = await api.GET("/api/v1/meta/modules");
    const targetHost = org?.custom_domain ?? `${org?.slug}.${meta?.base_domain ?? ""}`;
    if (targetHost === event.url.hostname) {
      event.cookies.set(IMPERSONATION_COOKIE, data.token, {
        path: "/",
        httpOnly: true,
        sameSite: "lax",
        secure: event.url.protocol === "https:",
        maxAge: 60 * 60,
      });
      throw redirect(303, "/");
    }
    const port = event.url.port ? `:${event.url.port}` : "";
    throw redirect(
      303,
      `${event.url.protocol}//${targetHost}${port}/impersonate?token=${encodeURIComponent(data.token)}`,
    );
  },
};
