import { fail, redirect } from "@sveltejs/kit";

import { parsePostedMinutes } from "$lib/core/duration";
import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

interface ItemDraft {
  title?: unknown;
  description?: unknown;
  priority?: unknown;
  relative_due_days?: unknown;
  allocated_minutes?: unknown;
  assignee_user_id?: unknown;
  assign_responsible?: unknown;
  requires_interaction?: unknown;
  checklist_title?: unknown;
  checklist_items?: unknown;
}

/** The item editor posts its rows as one JSON field; normalize them for the API. */
function parseItems(raw: string): {
  title: string;
  description: string | null;
  priority: "low" | "normal" | "high";
  relative_due_days: number | null;
  allocated_minutes: number | null;
  assignee_user_id: string | null;
  assign_responsible: boolean;
  requires_interaction: boolean;
  position: number;
  checklist_title: string | null;
  checklist_items: { title: string; description: string | null }[];
}[] {
  let drafts: ItemDraft[] = [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (Array.isArray(parsed)) drafts = parsed as ItemDraft[];
  } catch {
    drafts = [];
  }
  return drafts
    .map((draft, index) => {
      const title = String(draft.title ?? "").trim();
      const days = Number(draft.relative_due_days);
      return {
        title,
        description: String(draft.description ?? "").trim() || null,
        priority: (["low", "normal", "high"].includes(String(draft.priority))
          ? String(draft.priority)
          : "normal") as "low" | "normal" | "high",
        relative_due_days: Number.isFinite(days) && days >= 0 ? Math.floor(days) : null,
        // The row editor posts integer minutes, but a duration may also arrive as the text a
        // person typed (#326); one parser reads both.
        allocated_minutes: parsePostedMinutes(draft.allocated_minutes),
        assignee_user_id: String(draft.assignee_user_id ?? "").trim() || null,
        assign_responsible: draft.assign_responsible === true,
        requires_interaction: draft.requires_interaction === true,
        position: index,
        checklist_title: String(draft.checklist_title ?? "").trim() || null,
        // Item titles reshaped to `{title, description}` (issue #66). The bulk editor here enters
        // titles only; a per-item description authored on a task card is preserved on round-trip
        // because objects are accepted as well as bare strings.
        checklist_items: Array.isArray(draft.checklist_items)
          ? draft.checklist_items
              .map((ci) =>
                typeof ci === "string"
                  ? { title: ci.trim(), description: null }
                  : {
                      title: String((ci as { title?: unknown }).title ?? "").trim(),
                      description:
                        String((ci as { description?: unknown }).description ?? "").trim() || null,
                    },
              )
              .filter((ci) => ci.title)
          : [],
      };
    })
    .filter((item) => item.title);
}

function templateBody(form: FormData) {
  const trigger = String(form.get("trigger") ?? "manual") as "manual" | "company_status";
  return {
    name: String(form.get("name") ?? "").trim(),
    trigger,
    trigger_status:
      trigger === "company_status" ? String(form.get("trigger_status") ?? "").trim() || null : null,
    active: form.get("active") !== null,
    items: parseItems(String(form.get("items_json") ?? "[]")),
  };
}

/** Newline-separated titles → checklist template items (issue #66). Descriptions are authored on
 *  task cards, not in this bulk editor, so they default to null here. */
function parseChecklistItems(raw: FormDataEntryValue | null): {
  title: string;
  description: string | null;
}[] {
  return String(raw ?? "")
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean)
    .map((title) => ({ title, description: null }));
}

// Two org-wide repositories, each with its own write permission: task automation
// (`tasks.template.write`) and checklists (`tasks.checklist_template.write`). Every control here
// writes, so a visitor holding neither has nothing to do on the page — the tab is hidden and the
// route bounces, rather than rendering forms the API refuses (#244, CLAUDE.md §15). Each list is
// fetched only for the holder who can act on it: no call the caller would 403 on, and none paid
// for a section that will not render (docs/PERFORMANCE.md).
export const load: PageServerLoad = async (event) => {
  const canManageTemplates = can(event.locals.user, "tasks.template.write");
  const canManageChecklists = can(event.locals.user, "tasks.checklist_template.write");
  if (!canManageTemplates && !canManageChecklists) throw redirect(303, "/tasks");

  const api = apiFor(event);
  const [templates, checklistTemplates] = await Promise.all([
    canManageTemplates ? api.GET("/api/v1/tasks/templates").then((r) => r.data ?? []) : [],
    canManageChecklists
      ? api.GET("/api/v1/tasks/checklist-templates").then((r) => r.data ?? [])
      : [],
  ]);
  // Members come from the /tasks layout load.
  return { templates, checklistTemplates };
};

export const actions: Actions = {
  create: async (event) => {
    const body = templateBody(await event.request.formData());
    if (!body.name) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).POST("/api/v1/tasks/templates", { body });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { saved: true };
  },

  update: async (event) => {
    const form = await event.request.formData();
    const template_id = String(form.get("id") ?? "");
    const body = templateBody(form);
    if (!template_id || !body.name) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).PATCH("/api/v1/tasks/templates/{template_id}", {
      params: { path: { template_id } },
      body,
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { saved: true };
  },

  delete: async (event) => {
    const form = await event.request.formData();
    const template_id = String(form.get("id") ?? "");
    if (template_id) {
      await apiFor(event).DELETE("/api/v1/tasks/templates/{template_id}", {
        params: { path: { template_id } },
      });
    }
    return { deleted: true };
  },

  createChecklist: async (event) => {
    const form = await event.request.formData();
    const title = String(form.get("title") ?? "").trim();
    if (!title) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).POST("/api/v1/tasks/checklist-templates", {
      body: { title, items: parseChecklistItems(form.get("items")) },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { saved: true };
  },

  updateChecklist: async (event) => {
    const form = await event.request.formData();
    const template_id = String(form.get("id") ?? "");
    const title = String(form.get("title") ?? "").trim();
    if (!template_id || !title) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).PATCH("/api/v1/tasks/checklist-templates/{template_id}", {
      params: { path: { template_id } },
      body: { title, items: parseChecklistItems(form.get("items")) },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { saved: true };
  },

  deleteChecklist: async (event) => {
    const form = await event.request.formData();
    const template_id = String(form.get("id") ?? "");
    if (template_id) {
      await apiFor(event).DELETE("/api/v1/tasks/checklist-templates/{template_id}", {
        params: { path: { template_id } },
      });
    }
    return { deleted: true };
  },
};
