import "$lib/modules"; // ensure the panels are registered before we read the registry

import { error, fail, redirect } from "@sveltejs/kit";

import { apiBaseUrl } from "$lib/core/api/client";
import { apiErrorKey } from "$lib/core/errors";
import { entityPanelsFor } from "$lib/core/registry";
import { apiFor } from "$lib/core/session";
import { driveActions } from "$lib/modules/google/drive-actions.server";
import { interactionActions } from "$lib/modules/interactions/actions.server";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  const task_id = event.params.id;

  // Panels contributed by enabled modules (CLAUDE.md §6) — the same seam projects and
  // contacts compose; a task has no aggregate period, so `periodStart` is null.
  const context = { entityId: task_id, periodStart: null };
  const enabled = event.locals.theme?.enabledModules ?? [];
  const panels = entityPanelsFor(enabled, "task");

  // Lookups come from the /tasks layout load (data.labels doubles as allLabels).
  // The task keeps its own legacy TaskActivity trail, but contact-moment milestones (#152) are
  // mirrored onto the **core** activity log under entity_type=task — fetch those so the page's
  // activity feed can show "contactmoment gelogd" like the company/project/contact panels do.
  // A viewer without activity.read simply gets an empty list (openapi-fetch returns no data).
  const [{ data: task }, { data: checklistTemplates }, { data: files }, { data: hostActivity }, ...panelData] =
    await Promise.all([
      api.GET("/api/v1/tasks/{task_id}", { params: { path: { task_id } } }),
      api.GET("/api/v1/tasks/checklist-templates"),
      api.GET("/api/v1/files", {
        params: { query: { entity_type: "task", entity_id: task_id } },
      }),
      api.GET("/api/v1/activity", {
        params: { query: { entity_type: "task", entity_id: task_id, limit: 50 } },
      }),
      ...panels.map((panel) => panel.load(api, context)),
    ]);
  if (!task) throw error(404, { code: "not_found", message: "errors.not_found" });

  return {
    task,
    checklistTemplates: checklistTemplates ?? [],
    files: files ?? [],
    hostActivity: hostActivity ?? [],
    context,
    panels: panels.map((panel, index) => ({
      key: panel.key,
      titleKey: panel.titleKey,
      data: panelData[index],
    })),
  };
};

export const actions: Actions = {
  update: async (event) => {
    const form = await event.request.formData();
    if (form.has("title") && !String(form.get("title") ?? "").trim()) {
      return fail(400, { error: "errors.required" });
    }
    const body: Record<string, unknown> = {};
    // Only fields present in the submitting form are patched (partial updates).
    for (const field of [
      "title",
      "description",
      "status",
      "priority",
      "company_id",
      "project_id",
      "assignee_user_id",
      "due_date",
      "due_change_reason",
    ]) {
      if (form.has(field)) {
        const raw = String(form.get(field) ?? "").trim();
        body[field] = raw || null;
      }
    }
    if (form.has("allocated_minutes")) {
      const minutes = Number(String(form.get("allocated_minutes") ?? "").trim());
      body.allocated_minutes = Number.isFinite(minutes) && minutes > 0 ? Math.round(minutes) : null;
    }
    // Close policy (#157 extended): a hidden "false" precedes the checkbox, so a full edit-form
    // submit always carries a value (last wins); the status quick-form carries none → untouched.
    const requiresInteraction = form.getAll("requires_interaction");
    if (requiresInteraction.length > 0) {
      body.requires_interaction = requiresInteraction[requiresInteraction.length - 1] === "true";
    }
    if (form.has("freq")) {
      const freq = String(form.get("freq") ?? "").trim();
      body.recurrence = freq
        ? {
            freq: freq as "daily" | "weekly" | "monthly" | "quarterly" | "yearly",
            interval: Math.max(1, Number(form.get("interval") ?? 1) || 1),
            mode: String(form.get("mode") ?? "after_completion") as "after_completion" | "schedule",
          }
        : null;
    }
    const { error: apiError } = await apiFor(event).PATCH("/api/v1/tasks/{task_id}", {
      params: { path: { task_id: event.params.id } },
      body,
    });
    if (apiError) {
      // Prefer the field-specific reason (e.g. the closing contact-moment gate, #157) over the
      // generic "some fields are invalid" — the message is what tells the user what to do.
      const e = apiErrorKey(apiError);
      return fail(400, {
        error: e.fields?.status ?? e.fields?.closing_interaction_id ?? e.key,
      });
    }
    return { updated: true };
  },

  setRecurrence: async (event) => {
    const form = await event.request.formData();
    const freq = String(form.get("freq") ?? "").trim();
    const body = {
      recurrence: freq
        ? {
            freq: freq as "daily" | "weekly" | "monthly" | "quarterly" | "yearly",
            interval: Math.max(1, Number(form.get("interval") ?? 1) || 1),
            mode: String(form.get("mode") ?? "after_completion") as "after_completion" | "schedule",
          }
        : null,
    };
    const { error: apiError } = await apiFor(event).PATCH("/api/v1/tasks/{task_id}", {
      params: { path: { task_id: event.params.id } },
      body,
    });
    if (apiError) return fail(400, { error: apiErrorKey(apiError).key });
    return { updated: true };
  },

  setLabels: async (event) => {
    const form = await event.request.formData();
    const label_ids = form.getAll("label_ids").map(String).filter(Boolean);
    const { error: apiError } = await apiFor(event).PUT("/api/v1/tasks/{task_id}/labels", {
      params: { path: { task_id: event.params.id } },
      body: { label_ids },
    });
    if (apiError) return fail(400, { error: apiErrorKey(apiError).key });
    return { updated: true };
  },

  createLabel: async (event) => {
    const form = await event.request.formData();
    const name = String(form.get("name") ?? "").trim();
    if (!name) return fail(400, { error: "errors.required" });
    const api = apiFor(event);
    const { data: label, error: apiError } = await api.POST("/api/v1/tasks/labels", {
      body: { name, color: String(form.get("color") ?? "blue"), position: 0 },
    });
    if (apiError || !label) return fail(400, { error: apiErrorKey(apiError).key });
    // Attach the fresh label on top of the task's current set.
    const existing = form.getAll("current_label_ids").map(String).filter(Boolean);
    await api.PUT("/api/v1/tasks/{task_id}/labels", {
      params: { path: { task_id: event.params.id } },
      body: { label_ids: [...existing, label.id] },
    });
    return { updated: true };
  },

  addComment: async (event) => {
    const form = await event.request.formData();
    const body = String(form.get("body") ?? "").trim();
    if (!body) return fail(400, { error: "errors.required" });
    const { error: apiError } = await apiFor(event).POST("/api/v1/tasks/{task_id}/comments", {
      params: { path: { task_id: event.params.id } },
      body: { body },
    });
    if (apiError) return fail(400, { error: apiErrorKey(apiError).key });
    return { commented: true };
  },

  editComment: async (event) => {
    const form = await event.request.formData();
    const comment_id = String(form.get("comment_id") ?? "");
    const body = String(form.get("body") ?? "").trim();
    if (!comment_id || !body) return fail(400, { error: "errors.required" });
    const { error: apiError } = await apiFor(event).PATCH(
      "/api/v1/tasks/{task_id}/comments/{comment_id}",
      { params: { path: { task_id: event.params.id, comment_id } }, body: { body } },
    );
    if (apiError) return fail(400, { error: apiErrorKey(apiError).key });
    return { commented: true };
  },

  deleteComment: async (event) => {
    const form = await event.request.formData();
    const comment_id = String(form.get("comment_id") ?? "");
    if (comment_id) {
      await apiFor(event).DELETE("/api/v1/tasks/{task_id}/comments/{comment_id}", {
        params: { path: { task_id: event.params.id, comment_id } },
      });
    }
    return { commented: true };
  },

  addChecklist: async (event) => {
    const form = await event.request.formData();
    const title = String(form.get("title") ?? "").trim();
    const template_id = String(form.get("template_id") ?? "").trim();
    const description = String(form.get("description") ?? "").trim();
    if (!title && !template_id) return fail(400, { error: "errors.required" });
    const { error: apiError } = await apiFor(event).POST("/api/v1/tasks/{task_id}/checklists", {
      params: { path: { task_id: event.params.id } },
      body: {
        title: title || null,
        description: description || null,
        template_id: template_id || null,
      },
    });
    if (apiError) return fail(400, { error: apiErrorKey(apiError).key });
    return { checklist: true };
  },

  editChecklist: async (event) => {
    const form = await event.request.formData();
    const checklist_id = String(form.get("checklist_id") ?? "");
    const title = String(form.get("title") ?? "").trim();
    const description = String(form.get("description") ?? "").trim();
    if (!checklist_id || !title) return fail(400, { error: "errors.required" });
    const { error: apiError } = await apiFor(event).PATCH(
      "/api/v1/tasks/{task_id}/checklists/{checklist_id}",
      {
        params: { path: { task_id: event.params.id, checklist_id } },
        body: { title, description: description || null },
      },
    );
    if (apiError) return fail(400, { error: apiErrorKey(apiError).key });
    return { checklist: true };
  },

  addLink: async (event) => {
    const form = await event.request.formData();
    const url = String(form.get("url") ?? "").trim();
    if (!url) return fail(400, { error: "errors.required" });
    const { error: apiError } = await apiFor(event).POST("/api/v1/tasks/{task_id}/links", {
      params: { path: { task_id: event.params.id } },
      body: { url, title: String(form.get("title") ?? "").trim() || null },
    });
    if (apiError) return fail(400, { error: apiErrorKey(apiError).key });
    return { linkAdded: true };
  },

  deleteLink: async (event) => {
    const form = await event.request.formData();
    const link_id = String(form.get("link_id") ?? "");
    if (link_id) {
      await apiFor(event).DELETE("/api/v1/tasks/{task_id}/links/{link_id}", {
        params: { path: { task_id: event.params.id, link_id } },
      });
    }
    return { linkDeleted: true };
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
      `${apiBaseUrl()}/api/v1/files?entity_type=task&entity_id=${event.params.id}`,
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

  saveChecklistTemplate: async (event) => {
    const form = await event.request.formData();
    const title = String(form.get("title") ?? "").trim();
    // Items arrive as a JSON array of `{title, description}` (issue #66) so a checklist saved as a
    // template keeps its item descriptions, not just the titles.
    let items: { title: string; description: string | null }[] = [];
    try {
      const parsed = JSON.parse(String(form.get("items") ?? "[]")) as unknown;
      if (Array.isArray(parsed)) {
        items = parsed
          .map((i) => ({
            title: String((i as { title?: unknown }).title ?? "").trim(),
            description: String((i as { description?: unknown }).description ?? "").trim() || null,
          }))
          .filter((i) => i.title);
      }
    } catch {
      return fail(400, { error: "errors.validation" });
    }
    if (!title) return fail(400, { error: "errors.required" });
    const { error: apiError } = await apiFor(event).POST("/api/v1/tasks/checklist-templates", {
      body: { title, items },
    });
    if (apiError) return fail(400, { error: apiErrorKey(apiError).key });
    return { checklist: true };
  },

  deleteChecklist: async (event) => {
    const form = await event.request.formData();
    const checklist_id = String(form.get("checklist_id") ?? "");
    if (checklist_id) {
      await apiFor(event).DELETE("/api/v1/tasks/{task_id}/checklists/{checklist_id}", {
        params: { path: { task_id: event.params.id, checklist_id } },
      });
    }
    return { checklist: true };
  },

  addItem: async (event) => {
    const form = await event.request.formData();
    const checklist_id = String(form.get("checklist_id") ?? "");
    const title = String(form.get("title") ?? "").trim();
    const description = String(form.get("description") ?? "").trim();
    if (!checklist_id || !title) return fail(400, { error: "errors.required" });
    const { error: apiError } = await apiFor(event).POST(
      "/api/v1/tasks/{task_id}/checklists/{checklist_id}/items",
      {
        params: { path: { task_id: event.params.id, checklist_id } },
        body: { title, description: description || null },
      },
    );
    if (apiError) return fail(400, { error: apiErrorKey(apiError).key });
    return { checklist: true };
  },

  editItem: async (event) => {
    const form = await event.request.formData();
    const checklist_id = String(form.get("checklist_id") ?? "");
    const item_id = String(form.get("item_id") ?? "");
    const title = String(form.get("title") ?? "").trim();
    const description = String(form.get("description") ?? "").trim();
    if (!checklist_id || !item_id || !title) return fail(400, { error: "errors.required" });
    const { error: apiError } = await apiFor(event).PATCH(
      "/api/v1/tasks/{task_id}/checklists/{checklist_id}/items/{item_id}",
      {
        params: { path: { task_id: event.params.id, checklist_id, item_id } },
        body: { title, description: description || null },
      },
    );
    if (apiError) return fail(400, { error: apiErrorKey(apiError).key });
    return { checklist: true };
  },

  toggleItem: async (event) => {
    const form = await event.request.formData();
    const checklist_id = String(form.get("checklist_id") ?? "");
    const item_id = String(form.get("item_id") ?? "");
    if (checklist_id && item_id) {
      await apiFor(event).PATCH(
        "/api/v1/tasks/{task_id}/checklists/{checklist_id}/items/{item_id}",
        {
          params: { path: { task_id: event.params.id, checklist_id, item_id } },
          body: { done: form.get("done") === "true" },
        },
      );
    }
    return { checklist: true };
  },

  deleteItem: async (event) => {
    const form = await event.request.formData();
    const checklist_id = String(form.get("checklist_id") ?? "");
    const item_id = String(form.get("item_id") ?? "");
    if (checklist_id && item_id) {
      await apiFor(event).DELETE(
        "/api/v1/tasks/{task_id}/checklists/{checklist_id}/items/{item_id}",
        { params: { path: { task_id: event.params.id, checklist_id, item_id } } },
      );
    }
    return { checklist: true };
  },

  delete: async (event) => {
    await apiFor(event).DELETE("/api/v1/tasks/{task_id}", {
      params: { path: { task_id: event.params.id } },
    });
    throw redirect(303, "/tasks");
  },

  // Contactmomenten + Drive panel contracts (the panels post to their host page).
  ...interactionActions,
  ...driveActions,
};
