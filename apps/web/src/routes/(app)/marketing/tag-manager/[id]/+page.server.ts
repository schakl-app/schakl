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
 * But "waiting is the point" is an argument about the *lists*, and it had been applied to the
 * whole page: the heading, the client's name, the conversions schakl set up and every write
 * control sat behind six Google round trips. So the shell is what this load returns and the live
 * halves are **streamed** behind it (docs/PERFORMANCE.md) — the pending state saying "laden",
 * never the empty list, which is a different answer to a different question.
 *
 * The reads that stream are two, not six, and that is the other half of the fix. Each of
 * tags/triggers/variables/status resolved the workspace for itself — and resolving means listing
 * the container's workspaces — so the page cost nine Google requests where it now costs six, on
 * an API whose quota is counted **per user per minute**. `/workspace` answers the four at once.
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

  // Fired before anything is awaited and never awaited here: these are the slow ones, and the
  // shell below needs nothing they answer. `.catch` rather than a bare promise because a network
  // throw on a streamed value has no `{data, error}` to fall into and would take the page with it.
  const workspaceP = api
    .GET("/api/v1/gtm/containers/{container_id}/workspace", {
      params: { path: { container_id } },
    })
    .catch(() => ({ data: null, error: { detail: "network" } }));
  const versionsP = api
    .GET("/api/v1/gtm/containers/{container_id}/versions", {
      params: { path: { container_id } },
    })
    .catch(() => ({ data: null, error: { detail: "network" } }));

  const [container, conversions, companies] = await Promise.all([
    api.GET("/api/v1/gtm/containers/{container_id}", { params: { path: { container_id } } }),
    api.GET("/api/v1/gtm/containers/{container_id}/conversions", {
      params: { path: { container_id } },
    }),
    api.GET("/api/v1/companies", {
      params: { query: { limit: 200, offset: 0, count: false, sort: "name" } },
    }),
  ]);
  if (!container.data) throw error(404, "not_found");

  return {
    container: container.data,
    conversions: conversions.data ?? [],
    companies: companies.data?.items ?? [],
    /**
     * The workspace and the version history, behind the shell.
     *
     * One promise per section rather than one for both: the version list is a container-level
     * read and the workspace is four workspace-level ones, so they do not arrive together and
     * pretending otherwise would hold the faster one back.
     *
     * `liveError` travels *inside* each, not beside it — a refusal is something the streamed
     * half learns, and a top-level key would have to be resolved before the shell could render,
     * which is the thing this stopped doing. `apiErrorKey` falls back to a validation key for a
     * *null* error, so the presence check comes first.
     */
    workspace: workspaceP.then((r) => ({
      status: r.data?.status ?? null,
      tags: r.data?.tags ?? [],
      triggers: r.data?.triggers ?? [],
      variables: r.data?.variables ?? [],
      error: firstError(r.error),
    })),
    versions: versionsP.then((r) => ({ versions: r.data ?? [], error: firstError(r.error) })),
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
