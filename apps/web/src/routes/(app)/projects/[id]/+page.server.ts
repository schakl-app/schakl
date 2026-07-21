import "$lib/modules"; // ensure the panels are registered before we read the registry

import { error, fail, redirect } from "@sveltejs/kit";

import { apiBaseUrl } from "$lib/core/api/client";
import { parseAssignees } from "$lib/core/assignees";
import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { entityPanelsFor } from "$lib/core/registry";
import { apiFor } from "$lib/core/session";
import { interactionActions } from "$lib/modules/interactions/actions.server";
import { driveActions } from "$lib/modules/google/drive-actions.server";

import type { Actions, PageServerLoad } from "./$types";

function numberOrNull(raw: FormDataEntryValue | null): number | null {
  const s = String(raw ?? "").trim();
  if (!s) return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}

function parseCustom(raw: FormDataEntryValue | null): Record<string, unknown> {
  try {
    return JSON.parse(String(raw ?? "{}")) as Record<string, unknown>;
  } catch {
    return {};
  }
}

export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  const project_id = event.params.id;

  // `hours=true` costs one grouped query and replaces the separate `/time/logged` call this page
  // used to make. It also carries the period start the API resolved in Europe/Amsterdam — the
  // browser used to recompute that in UTC, which lands on the wrong day for half the year. The
  // budget bar and the Uren panel below it now count from exactly the same instant (#43).
  const { data: project } = await api.GET("/api/v1/projects/{project_id}", {
    params: { path: { project_id }, query: { hours: true } },
  });
  if (!project) throw error(404, { code: "not_found", message: "errors.not_found" });

  const periodStart = project.hours?.period_start ?? null;
  const context = { entityId: project_id, periodStart };

  // Panels contributed by the enabled modules (CLAUDE.md §6). A tenant without `time` gets no
  // Uren panel and pays for no call — the loaders below simply don't exist.
  const enabled = event.locals.theme?.enabledModules ?? [];
  const panels = entityPanelsFor(enabled, "project");

  // Cost from employee rates (#111) is salary-derived: fetched only for someone the API would
  // let see it (the guard is UX; the API stays the boundary), and only inside the same flight.
  const canSeeCost =
    can(event.locals.user, "time.report.read") && can(event.locals.user, "leave.rate.read", "any");

  // Every call in one flight. `projects` is a name-only lookup: the panel's edit modal needs the
  // picker, and `count=false` skips the COUNT(*) it would throw away.
  const [tasks, companies, projects, members, statuses, definitions, cost, files, ...panelData] =
    await Promise.all([
      api.GET("/api/v1/tasks", { params: { query: { project_id, limit: 200, offset: 0 } } }),
      api.GET("/api/v1/companies", { params: { query: { limit: 200, offset: 0, count: false } } }),
      api.GET("/api/v1/projects", { params: { query: { limit: 200, offset: 0, count: false } } }),
      api.GET("/api/v1/members/lookup"),
      // The tenant's task statuses (issue #62) so the to-do list groups/toggles by the real ones.
      api.GET("/api/v1/tasks/statuses"),
      api.GET("/api/v1/custom-fields/definitions", {
        params: { query: { entity_type: "project" } },
      }),
      canSeeCost
        ? api.GET("/api/v1/time/cost", { params: { query: { project_id } } })
        : Promise.resolve({ data: null }),
      api.GET("/api/v1/files", {
        params: { query: { entity_type: "project", entity_id: project_id } },
      }),
      ...panels.map((panel) => panel.load(api, context)),
    ]);

  return {
    project,
    files: files.data ?? [],
    cost: cost.data ?? null,
    tasks: tasks.data?.items ?? [],
    companies: companies.data?.items ?? [],
    projects: projects.data?.items ?? [],
    members: members.data ?? [],
    statuses: statuses.data ?? [],
    definitions: definitions.data ?? [],
    context,
    // Keyed so the page can pair each payload with the spec that produced it, without the page
    // knowing what any of them are.
    panels: panels.map((panel, index) => ({
      key: panel.key,
      titleKey: panel.titleKey,
      // Carried so the page can hold the trail (90) under To-dos/Documents — history sits
      // below the working surfaces (docs/UX.md principle 4).
      position: panel.position ?? 100,
      data: panelData[index],
    })),
    locale: event.locals.locale,
  };
};

export const actions: Actions = {
  update: async (event) => {
    const form = await event.request.formData();
    const { error: apiError } = await apiFor(event).PATCH("/api/v1/projects/{project_id}", {
      params: { path: { project_id: event.params.id } },
      body: {
        name: String(form.get("name") ?? "").trim() || undefined,
        description: String(form.get("description") ?? "").trim() || null,
        assignees: parseAssignees(form.get("assignees")),
        status: String(form.get("status") ?? "active") as "active",
        billable_default: form.get("billable_default") !== null,
        // Absent when a linked subscription sources the hours (#225): the fields render
        // disabled, a disabled input never posts, and the API refuses the write anyway.
        budget_period: form.has("budget_period")
          ? (String(form.get("budget_period") ?? "total") as "total")
          : undefined,
        budget_hours: form.has("budget_hours") ? numberOrNull(form.get("budget_hours")) : undefined,
        budget_amount: numberOrNull(form.get("budget_amount")),
        start_date: String(form.get("start_date") ?? "").trim() || null,
        end_date: String(form.get("end_date") ?? "").trim() || null,
        custom: parseCustom(form.get("custom")),
      },
    });
    if (apiError) return fail(400, { error: apiErrorKey(apiError).key });
    return { updated: true };
  },

  addTask: async (event) => {
    const form = await event.request.formData();
    const title = String(form.get("title") ?? "").trim();
    if (!title) return fail(400, { error: "errors.required" });
    const company_id = String(form.get("company_id") ?? "").trim();
    const { error: apiError } = await apiFor(event).POST("/api/v1/tasks", {
      body: {
        title,
        // Status omitted → the API assigns the org's default status (issue #62).
        priority: "normal",
        project_id: event.params.id,
        company_id: company_id || null,
        requires_interaction: false,
        visible_to_client: false,
      },
    });
    if (apiError) return fail(400, { error: apiErrorKey(apiError).key });
    return { taskAdded: true };
  },

  reorderTask: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    const position = Number(form.get("position"));
    if (id && Number.isFinite(position)) {
      await apiFor(event).PATCH("/api/v1/tasks/{task_id}", {
        params: { path: { task_id: id } },
        body: { position },
      });
    }
    return { taskReordered: true };
  },

  toggleTask: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    // A configured status key (issue #62); the row computes which from the org's vocabulary.
    const status = String(form.get("status") ?? "").trim();
    if (id && status) {
      await apiFor(event).PATCH("/api/v1/tasks/{task_id}", {
        params: { path: { task_id: id } },
        body: { status },
      });
    }
    return { taskToggled: true };
  },

  deleteTask: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (id) {
      await apiFor(event).DELETE("/api/v1/tasks/{task_id}", {
        params: { path: { task_id: id } },
      });
    }
    return { taskDeleted: true };
  },

  deleteProject: async (event) => {
    await apiFor(event).DELETE("/api/v1/projects/{project_id}", {
      params: { path: { project_id: event.params.id } },
    });
    throw redirect(303, "/projects");
  },

  /** Document attachment (#123): multipart through a plain fetch — the typed client has no
   *  multipart serializer — with the same cookie + tenant host the client would send. */
  uploadFile: async (event) => {
    const form = await event.request.formData();
    const upload = form.get("file");
    if (!(upload instanceof File) || upload.size === 0) {
      return fail(400, { fileError: "errors.required" });
    }
    const body = new FormData();
    body.append("file", upload, upload.name);
    const res = await event.fetch(
      `${apiBaseUrl()}/api/v1/files?entity_type=project&entity_id=${event.params.id}`,
      {
        method: "POST",
        headers: {
          cookie: event.request.headers.get("cookie") ?? "",
          "x-forwarded-host": event.request.headers.get("host") ?? "",
        },
        body,
      },
    );
    if (!res.ok) {
      return fail(400, {
        fileError: res.status === 413 ? "errors.upload_too_large" : "errors.upload_type",
      });
    }
    return { fileUploaded: true };
  },

  deleteFile: async (event) => {
    const form = await event.request.formData();
    const file_id = String(form.get("file_id") ?? "");
    if (file_id) {
      await apiFor(event).DELETE("/api/v1/files/{file_id}", {
        params: { path: { file_id } },
      });
    }
    return { fileDeleted: true };
  },

  // The Uren panel's ⋯ menu posts here (its host contract). Identical to the Uren report's
  // actions, and just as thin: the API decides who may edit an approved or someone else's entry.
  updateEntry: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    const date = String(form.get("date") ?? "").trim();
    const start = String(form.get("start") ?? "").trim();
    const end = String(form.get("end") ?? "").trim();
    if (!id || !date || !start || !end) return fail(400, { error: "errors.required" });

    const { error: apiError } = await apiFor(event).PATCH("/api/v1/time/entries/{entry_id}", {
      params: { path: { entry_id: id } },
      body: {
        started_at: `${date}T${start}:00Z`,
        ended_at: `${date}T${end}:00Z`,
        break_minutes: Number(form.get("break_minutes") ?? 0) || 0,
        description: String(form.get("description") ?? "").trim() || null,
        company_id: String(form.get("company_id") ?? "").trim() || null,
        project_id: String(form.get("project_id") ?? "").trim() || null,
        task_id: String(form.get("task_id") ?? "").trim() || null,
        billable: form.get("billable") !== "false",
        entry_type_key: String(form.get("entry_type_key") ?? "").trim() || null,
      },
    });
    if (apiError) return fail(400, { error: apiErrorKey(apiError).key });
    return { entryUpdated: true };
  },

  deleteEntry: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (id) {
      const { error: apiError } = await apiFor(event).DELETE("/api/v1/time/entries/{entry_id}", {
        params: { path: { entry_id: id } },
      });
      if (apiError) return fail(400, { error: apiErrorKey(apiError).key });
    }
    return { entryDeleted: true };
  },

  // Contactmomenten panel contract (lib/modules/interactions).
  ...interactionActions,
  // Drive panel contract (lib/modules/google).
  ...driveActions,
};
