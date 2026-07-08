import { error, fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  const task_id = event.params.id;

  const { data: task } = await api.GET("/api/v1/tasks/{task_id}", {
    params: { path: { task_id } },
  });
  if (!task) throw error(404, { code: "not_found", message: "errors.not_found" });

  // Lookups come from the /tasks layout load (data.labels doubles as allLabels).
  const { data: checklistTemplates } = await api.GET("/api/v1/tasks/checklist-templates");

  return {
    task,
    checklistTemplates: checklistTemplates ?? [],
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
    if (form.has("freq")) {
      const freq = String(form.get("freq") ?? "").trim();
      body.recurrence = freq
        ? {
            freq: freq as "daily" | "weekly" | "monthly" | "quarterly" | "yearly",
            interval: Math.max(1, Number(form.get("interval") ?? 1) || 1),
            mode: String(form.get("mode") ?? "after_completion") as
              | "after_completion"
              | "schedule",
          }
        : null;
    }
    const { error: apiError } = await apiFor(event).PATCH("/api/v1/tasks/{task_id}", {
      params: { path: { task_id: event.params.id } },
      body,
    });
    if (apiError) return fail(400, { error: apiErrorKey(apiError).key });
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
            mode: String(form.get("mode") ?? "after_completion") as
              | "after_completion"
              | "schedule",
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
    if (!title && !template_id) return fail(400, { error: "errors.required" });
    const { error: apiError } = await apiFor(event).POST("/api/v1/tasks/{task_id}/checklists", {
      params: { path: { task_id: event.params.id } },
      body: { title: title || null, template_id: template_id || null },
    });
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

  saveChecklistTemplate: async (event) => {
    const form = await event.request.formData();
    const title = String(form.get("title") ?? "").trim();
    const items = String(form.get("items") ?? "")
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
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
    if (!checklist_id || !title) return fail(400, { error: "errors.required" });
    const { error: apiError } = await apiFor(event).POST(
      "/api/v1/tasks/{task_id}/checklists/{checklist_id}/items",
      { params: { path: { task_id: event.params.id, checklist_id } }, body: { title } },
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
};
