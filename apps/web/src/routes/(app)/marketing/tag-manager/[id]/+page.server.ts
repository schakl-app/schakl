import { error, fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

/**
 * One container: what is in the workspace schakl writes in, what is live, and what was set up
 * from here.
 *
 * Everything except the container row and the conversions is a **live** Google call, which is
 * the deliberate opposite of the company panel: a tag list that is a mirror answers the wrong
 * question, because half the edits to it are made in the Tag Manager interface by people who do
 * not work here. Waiting is the point on this page, and a surprise on the client page.
 *
 * The four reads are fanned rather than sequenced, and a refusal on any of them is *not* fatal:
 * a container whose grant lost the publish scope should still show its tags. Each one keeps its
 * own error so the section that failed can say so instead of the page going blank.
 */
/** The first refusal among several calls, as an i18n key — or `null` when they all answered. */
function firstError(...errors: unknown[]): string | null {
  const failed = errors.find(Boolean);
  return failed ? apiErrorKey(failed).key : null;
}

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "google_tag_manager.container.read")) throw redirect(303, "/");
  const api = apiFor(event);
  const container_id = event.params.id;

  const container = await api.GET("/api/v1/gtm/containers/{container_id}", {
    params: { path: { container_id } },
  });
  if (!container.data) throw error(404, "not_found");

  const [tags, triggers, variables, versions, conversions, status, companies] = await Promise.all([
    api.GET("/api/v1/gtm/containers/{container_id}/tags", { params: { path: { container_id } } }),
    api.GET("/api/v1/gtm/containers/{container_id}/triggers", {
      params: { path: { container_id } },
    }),
    api.GET("/api/v1/gtm/containers/{container_id}/variables", {
      params: { path: { container_id } },
    }),
    api.GET("/api/v1/gtm/containers/{container_id}/versions", {
      params: { path: { container_id } },
    }),
    api.GET("/api/v1/gtm/containers/{container_id}/conversions", {
      params: { path: { container_id } },
    }),
    api.GET("/api/v1/gtm/containers/{container_id}/status", {
      params: { path: { container_id } },
    }),
    api.GET("/api/v1/companies", {
      params: { query: { limit: 200, offset: 0, count: false, sort: "name" } },
    }),
  ]);

  return {
    container: container.data,
    tags: tags.data ?? [],
    triggers: triggers.data ?? [],
    variables: variables.data ?? [],
    versions: versions.data ?? [],
    conversions: conversions.data ?? [],
    status: status.data ?? null,
    companies: companies.data?.items ?? [],
    // The first refusal any section met, so the page can say what happened rather than render
    // five empty lists that look exactly like an empty container. `apiErrorKey` falls back to a
    // validation key for a *null* error, so the presence check comes first.
    liveError: firstError(tags.error, triggers.error, versions.error, status.error),
    // Mirrors the key each call actually makes (#310), never the one the page is about.
    canWrite: can(event.locals.user, "google_tag_manager.tag.write"),
    canPublish: can(event.locals.user, "google_tag_manager.version.publish"),
    canManage: can(event.locals.user, "google_tag_manager.settings.manage"),
  };
};

export const actions: Actions = {
  conversion: async (event) => {
    const form = await event.request.formData();
    const kind = String(form.get("kind") ?? "ga4_event");
    const triggerKind = String(form.get("trigger_kind") ?? "page_view");
    const { error: failure } = await apiFor(event).POST(
      "/api/v1/gtm/containers/{container_id}/conversions",
      {
        params: { path: { container_id: event.params.id } },
        body: {
          name: String(form.get("name") ?? "").trim(),
          kind,
          trigger: {
            // The trigger gets its name from the conversion; a second name field would be one
            // more thing to fill in that nobody ever looks at afterwards.
            name: String(form.get("name") ?? "").trim(),
            kind: triggerKind,
            url_contains: String(form.get("url_contains") ?? "").trim() || null,
            event_name: String(form.get("trigger_event_name") ?? "").trim() || null,
            selector: String(form.get("selector") ?? "").trim() || null,
          },
          event_name: String(form.get("event_name") ?? "").trim() || null,
          measurement_id: String(form.get("measurement_id") ?? "").trim() || null,
          conversion_id: String(form.get("conversion_id") ?? "").trim() || null,
          conversion_label: String(form.get("conversion_label") ?? "").trim() || null,
          currency_code: String(form.get("currency_code") ?? "").trim() || null,
        },
      },
    );
    if (failure) return fail(400, { error: apiErrorKey(failure).key });
    return { conversionCreated: String(form.get("name") ?? "") };
  },

  version: async (event) => {
    const form = await event.request.formData();
    const { data, error: failure } = await apiFor(event).POST(
      "/api/v1/gtm/containers/{container_id}/versions",
      {
        params: { path: { container_id: event.params.id } },
        body: { name: String(form.get("name") ?? "").trim(), notes: "" },
      },
    );
    if (failure) return fail(400, { error: apiErrorKey(failure).key });
    // "Nothing to freeze" is a 201 with no version, not a failure — and telling the user it
    // worked when no version exists is how somebody then goes looking for one to publish.
    if (data?.empty) return { versionEmpty: true };
    return { versionCreated: data?.version_id ?? "" };
  },

  publish: async (event) => {
    const form = await event.request.formData();
    const { data, error: failure } = await apiFor(event).POST(
      "/api/v1/gtm/containers/{container_id}/versions/{version_id}/publish",
      {
        params: {
          path: {
            container_id: event.params.id,
            version_id: String(form.get("version_id") ?? ""),
          },
        },
      },
    );
    if (failure) return fail(400, { error: apiErrorKey(failure).key });
    return { published: data?.version_id ?? "" };
  },

  deleteTag: async (event) => {
    const form = await event.request.formData();
    const { error: failure } = await apiFor(event).DELETE(
      "/api/v1/gtm/containers/{container_id}/tags/{tag_id}",
      {
        params: {
          path: { container_id: event.params.id, tag_id: String(form.get("tag_id") ?? "") },
        },
      },
    );
    if (failure) return fail(400, { error: apiErrorKey(failure).key });
    return { tagDeleted: true };
  },

  verify: async (event) => {
    const { error: failure } = await apiFor(event).POST(
      "/api/v1/gtm/containers/{container_id}/verify",
      { params: { path: { container_id: event.params.id } } },
    );
    if (failure) return fail(400, { error: apiErrorKey(failure).key });
    return { verified: true };
  },
};
