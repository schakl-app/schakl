import "$lib/modules"; // ensure the panels are registered before we read the registry

import { error, fail, redirect } from "@sveltejs/kit";

import { parseAssignees } from "$lib/core/assignees";
import { parsePostedMinutes } from "$lib/core/duration";
import { apiErrorKey } from "$lib/core/errors";
import { checked } from "$lib/core/forms";
import { originOf } from "$lib/core/origin";
import { can } from "$lib/core/permissions";
import { createCompanyAction } from "$lib/core/quickcreate.server";
import { entityPanelsFor } from "$lib/core/registry";
import { apiFor } from "$lib/core/session";
import { driveActions } from "$lib/integrations/google/drive-actions.server";
import { fileActions } from "$lib/core/files/actions.server";
import { interactionActions } from "$lib/modules/interactions/actions.server";
import {
  createScheduleAction,
  deleteScheduleAction,
  logScheduleTimeAction,
  updateScheduleAction,
} from "$lib/modules/tasks/schedule-actions.server";

import type { components } from "$lib/core/api/schema";

import type { Actions, PageServerLoad } from "./$types";

/** A hidden field's comma-joined ids, cleaned — the reorder forms' one input shape. */
function idList(raw: FormDataEntryValue | null): string[] {
  return String(raw ?? "")
    .split(",")
    .map((id) => id.trim())
    .filter(Boolean);
}

/** The rule shape the API takes, straight off the generated client — never a hand-kept copy. */
type Rule = components["schemas"]["Recurrence"];
type PlanBlocks = NonNullable<NonNullable<Rule["plan"]>["blocks"]>;

/** An optional whole number a `<select>`/`<input type=number>` posts, or `null` for "not set". */
function optionalInt(form: FormData, name: string): number | null {
  const raw = String(form.get(name) ?? "").trim();
  if (raw === "") return null;
  const value = Number(raw);
  return Number.isFinite(value) ? Math.trunc(value) : null;
}

/**
 * The repeat rule the editor composed (#335).
 *
 * An empty `freq` is "Herhaalt niet" and clears the rule. An anchor left on its "volg de
 * vervaldatum" option is **absent**, not zero: the API reads absence as the pre-#335 behaviour
 * (the cadence hangs off the due date), and a `0` would mean Monday.
 *
 * The auto-plan reads *presence* (`$lib/core/forms.checked`), never a particular posted value —
 * an unticked checkbox posts nothing at all, and comparing against a literal is how a whole
 * module came to post `false` whatever the user ticked (the reporting post-mortem).
 */
function readRecurrence(form: FormData): Rule | null {
  const freq = String(form.get("freq") ?? "").trim();
  if (!freq) return null;
  const rule: Rule = {
    freq: freq as "daily" | "weekly" | "monthly" | "quarterly" | "yearly",
    interval: Math.max(1, Number(form.get("interval") ?? 1) || 1),
    mode: String(form.get("mode") ?? "after_completion") as "after_completion" | "schedule",
  };
  const weekday = optionalInt(form, "on_weekday");
  const day = optionalInt(form, "on_day");
  const month = optionalInt(form, "on_month");
  const week = optionalInt(form, "on_week");
  if (weekday !== null) rule.on_weekday = weekday;
  // An n-th weekday is a pair (the API refuses half of one); the editor posts both or neither.
  if (week !== null && weekday !== null) rule.on_week = week;
  // A yearly anchor is a whole date or nothing (the API refuses half of one), so the pair goes
  // together — and the editor only ever shows both boxes at once.
  if (day !== null && (freq !== "yearly" || month !== null)) rule.on_day = day;
  if (month !== null && (day !== null || week !== null) && freq === "yearly") {
    rule.on_month = month;
  }

  // The plan travels as one JSON field: a list of placed blocks is not a shape a flat form can
  // post as fields, and the editor is the one place that composes it. Presence of the checkbox
  // is read with `checked`, never a literal (the reporting post-mortem); the API validates every
  // block, so this only has to be honest about the shape.
  if (checked(form, "plan_enabled")) {
    let parsed: unknown;
    try {
      parsed = JSON.parse(String(form.get("plan_blocks") ?? "[]"));
    } catch {
      parsed = [];
    }
    const blocks = Array.isArray(parsed) ? (parsed as PlanBlocks) : [];
    if (blocks.length > 0) rule.plan = { blocks };
  }
  return rule;
}

export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  const task_id = event.params.id;

  // Panels contributed by enabled modules (CLAUDE.md §6) — the same seam projects and
  // contacts compose; a task has no aggregate period, so `periodStart` is null.
  const context = { entityId: task_id, periodStart: null };
  const enabled = event.locals.theme?.enabledModules ?? [];
  const panels = entityPanelsFor(enabled, "task", event.locals.user);

  // Lookups come from the /tasks layout load (data.labels doubles as allLabels).
  // The task keeps its own legacy TaskActivity trail, but contact-moment milestones (#152) are
  // mirrored onto the **core** activity log under entity_type=task — fetch those so the page's
  // activity feed can show "contactmoment gelogd" like the company/project/contact panels do.
  // A viewer without activity.read simply gets an empty list (openapi-fetch returns no data).
  const [
    { data: task },
    checklistTemplates,
    { data: files },
    { data: hostActivity },
    { data: schedules },
    { data: companyDefinitions },
    ...panelData
  ] = await Promise.all([
    api.GET("/api/v1/tasks/{task_id}", { params: { path: { task_id } } }),
    // Only someone who may edit the task can pour a checklist template into it, and the API
    // now says so too — a portal client (#193) has no business enumerating the agency's
    // process library, and nobody should pay for a call whose picker will not render.
    can(event.locals.user, "tasks.task.write")
      ? api.GET("/api/v1/tasks/checklist-templates").then((r) => r.data ?? [])
      : [],
    api.GET("/api/v1/files", {
      params: { query: { entity_type: "task", entity_id: task_id } },
    }),
    // …and never for a client: the trail is the agency's own record of the work, the page
    // draws no section for one, and the API answers a portal login with an empty feed anyway
    // (docs/PORTAL.md). A round trip whose answer is thrown away is a round trip.
    event.locals.user?.isPortal
      ? { data: [] }
      : api.GET("/api/v1/activity", {
          params: { query: { entity_type: "task", entity_id: task_id, limit: 50 } },
        }),
    // Every planned block for this task (#188) — no date window, the panel wants the lot. A
    // viewer without schedule.read simply gets an empty list.
    api.GET("/api/v1/tasks/schedules", { params: { query: { task_id } } }),
    // For the inline company quick-create (#115): the full dialog includes custom fields.
    api.GET("/api/v1/custom-fields/definitions", {
      params: { query: { entity_type: "company" } },
    }),
    ...panels.map((panel) => panel.load(api, context)),
  ]);
  if (!task) throw error(404, { code: "not_found", message: "errors.not_found" });

  return {
    task,
    checklistTemplates,
    files: files ?? [],
    hostActivity: hostActivity ?? [],
    schedules: schedules ?? [],
    companyDefinitions: companyDefinitions ?? [],
    context,
    // `position` travels with the panel (#393): the page interleaves its own sections with
    // these on one scale, so Drive can sit above Reacties without `google` being edited.
    // `entityPanelsFor` already sorted on the same fallback, so a panel that declares none
    // reads the same number here as it did there.
    panels: panels.map((panel, index) => ({
      key: panel.key,
      titleKey: panel.titleKey,
      position: panel.position ?? 100,
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
    // A deadline may be moved and may not be removed (#392). The field is `required`, so this
    // is the non-browser backstop rather than the thing a user meets — but the loop below turns
    // every empty value into an explicit `null`, which is exactly the one the API refuses, and
    // "errors.validation" over a blank box is a worse sentence than the field's own.
    if (form.has("due_date") && !String(form.get("due_date") ?? "").trim()) {
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
      // The assignee picker (#273) always posts both sides (one empty), so switching between
      // employees and a client contact actively clears the other — the API rejects both at once.
      "assignee_contact_id",
      "due_date",
      "due_change_reason",
    ]) {
      if (form.has(field)) {
        const raw = String(form.get(field) ?? "").trim();
        body[field] = raw || null;
      }
    }
    // The roster (#375), one hidden JSON field for the whole thing — an edit surface has one save
    // button. Absent (a form that does not render the picker, like the status quick-form) leaves
    // the assignees alone; `[]` is the picker's way of saying "nobody", which is a real edit.
    const assignees = parseAssignees(form.get("assignees"));
    if (assignees !== undefined) body.assignees = assignees;
    // The budget travels as the text that was typed ("1:40"), so the browser is not the authority
    // on what it means (#326): the same parser runs here, and a bare number still reads as minutes.
    if (form.has("allocated_minutes")) {
      body.allocated_minutes = parsePostedMinutes(form.get("allocated_minutes"));
    }
    // Close policy (#157 extended): a hidden "false" precedes the checkbox, so a full edit-form
    // submit always carries a value (last wins); the status quick-form carries none → untouched.
    const requiresInteraction = form.getAll("requires_interaction");
    if (requiresInteraction.length > 0) {
      body.requires_interaction = requiresInteraction[requiresInteraction.length - 1] === "true";
    }
    // Client-portal visibility rides the same hidden-false-then-checkbox pattern.
    const visibleToClient = form.getAll("visible_to_client");
    if (visibleToClient.length > 0) {
      body.visible_to_client = visibleToClient[visibleToClient.length - 1] === "true";
    }
    if (form.has("freq")) {
      body.recurrence = readRecurrence(form);
    }
    // "Ook de uren registreren" (#314): the entry rides along on the finish, in one request and
    // one transaction — a finished task whose hours were lost to a second, failed call is the
    // exact thing this exists to prevent. Times are the time module's wall-clock-as-UTC
    // convention on the dialog's own date, like the interaction ride-along (#175/#184).
    const logDate = String(form.get("log_date") ?? "").trim();
    const logStart = String(form.get("log_start") ?? "").trim();
    const logEnd = String(form.get("log_end") ?? "").trim();
    if (form.get("log_time") === "1" && logDate && logStart && logEnd) {
      const scheduleId = String(form.get("log_schedule_id") ?? "").trim();
      body.log_time = {
        started_at: `${logDate}T${logStart}:00Z`,
        ended_at: `${logDate}T${logEnd}:00Z`,
        // Blank falls back to the task's title, server-side — so an MCP or script caller gets
        // the same row a person would.
        description: String(form.get("log_description") ?? "").trim() || null,
        // The planned block these hours confirm (#188), so it stops offering them again.
        schedule_id: scheduleId || null,
      };
    }
    const api = apiFor(event);
    const { error: apiError } = await api.PATCH("/api/v1/tasks/{task_id}", {
      params: { path: { task_id: event.params.id } },
      body,
    });
    if (apiError) {
      // Prefer the field-specific reason (e.g. the closing contact-moment gate, #157) over the
      // generic "some fields are invalid" — the message is what tells the user what to do.
      const e = apiErrorKey(apiError);
      return fail(400, {
        error:
          e.fields?.status ??
          e.fields?.closing_interaction_id ??
          e.fields?.assignee_contact_id ??
          // …and the roster emptied to nobody, which the API refuses (somebody is always on
          // a task) and the picker cannot say for itself, its field being hidden.
          e.fields?.assignee_user_id ??
          // …and the missing reason for a moved deadline, which the in-place editor prints
          // beside the date it refused.
          e.fields?.due_change_reason ??
          e.fields?.log_time ??
          e.key,
      });
    }
    // The blocks a finished task left standing (#335 F6). **After** the finish, never before: a
    // status move the API refuses must not take somebody's calendar with it. Through the ordinary
    // delete route, so `task_schedule.removed` fires and the pushed Google event goes too — the
    // one thing a raw row delete could never do (#188's one-emit-site rule).
    for (const schedule_id of idList(form.get("remove_schedule_ids"))) {
      await api.DELETE("/api/v1/tasks/schedules/{schedule_id}", {
        params: { path: { schedule_id } },
      });
    }
    return { updated: true };
  },

  /** The rule on its own (no other field touched) — one reader, so the two cannot drift. */
  setRecurrence: async (event) => {
    const form = await event.request.formData();
    const { error: apiError } = await apiFor(event).PATCH("/api/v1/tasks/{task_id}", {
      params: { path: { task_id: event.params.id } },
      body: { recurrence: readRecurrence(form) },
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

  // One action for both, because posting a reply *is* posting a comment (#312): a second action
  // would be a second place to keep the sanitising, the error key and the busy contract in step.
  // An empty `parent_id` is a thread opener — the reply form simply carries the field.
  addComment: async (event) => {
    const form = await event.request.formData();
    const body = String(form.get("body") ?? "").trim();
    const parent_id = String(form.get("parent_id") ?? "") || null;
    if (!body) return fail(400, { error: "errors.required" });
    const { data, error: apiError } = await apiFor(event).POST("/api/v1/tasks/{task_id}/comments", {
      params: { path: { task_id: event.params.id } },
      body: { body, parent_id },
    });
    if (apiError) return fail(400, { error: apiErrorKey(apiError).key });
    // What was just written, so the card can mark it. Reading oldest-first it lands at the far
    // end of a long list and the composer is at the top, which left "did that send?" as a
    // question a successful save should never leave open.
    return { commented: true, comment_id: data?.id ?? null };
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

  /** Copy a checklist beside its source (title, description, items — never the ticks). The
   *  title is the user's: the API deliberately invents no "(kopie)" suffix. */
  duplicateChecklist: async (event) => {
    const form = await event.request.formData();
    const checklist_id = String(form.get("checklist_id") ?? "");
    const title = String(form.get("title") ?? "").trim();
    if (!checklist_id || !title) return fail(400, { error: "errors.required" });
    const { error: apiError } = await apiFor(event).POST(
      "/api/v1/tasks/{task_id}/checklists/{checklist_id}/duplicate",
      {
        params: { path: { task_id: event.params.id, checklist_id } },
        body: { title },
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

  // Document attachments (#123): the shared strip actions, one copy per host record.
  ...fileActions("task"),

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

  // The one write on this page that happens over and over, so it is the one that may not cost a
  // reload: the checkbox flips optimistically in the browser and nothing is invalidated
  // (docs/PERFORMANCE.md). That makes reporting the refusal this action's job — a swallowed
  // error used to be survivable because the reload put the box back, and now nothing would.
  toggleItem: async (event) => {
    const form = await event.request.formData();
    const checklist_id = String(form.get("checklist_id") ?? "");
    const item_id = String(form.get("item_id") ?? "");
    if (!checklist_id || !item_id) return fail(400, { error: "errors.required" });
    const { error: apiError } = await apiFor(event).PATCH(
      "/api/v1/tasks/{task_id}/checklists/{checklist_id}/items/{item_id}",
      {
        params: { path: { task_id: event.params.id, checklist_id, item_id } },
        body: { done: form.get("done") === "true" },
      },
    );
    if (apiError) return fail(400, { error: apiErrorKey(apiError).key });
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

  // Both reorders post the *whole* new order as one comma-joined id list — the shape the API
  // takes (`ChecklistOrder`), and the shape a drag and an arrow press produce alike, so neither
  // gesture can leave half an order behind.
  reorderChecklists: async (event) => {
    const form = await event.request.formData();
    const checklist_ids = idList(form.get("ids"));
    if (checklist_ids.length === 0) return fail(400, { error: "errors.required" });
    const { error: apiError } = await apiFor(event).POST(
      "/api/v1/tasks/{task_id}/checklists/order",
      { params: { path: { task_id: event.params.id } }, body: { checklist_ids } },
    );
    if (apiError) return fail(400, { error: apiErrorKey(apiError).key });
    return { checklist: true };
  },

  reorderItems: async (event) => {
    const form = await event.request.formData();
    const checklist_id = String(form.get("checklist_id") ?? "");
    const item_ids = idList(form.get("ids"));
    if (!checklist_id || item_ids.length === 0) return fail(400, { error: "errors.required" });
    const { error: apiError } = await apiFor(event).POST(
      "/api/v1/tasks/{task_id}/checklists/{checklist_id}/items/order",
      { params: { path: { task_id: event.params.id, checklist_id } }, body: { item_ids } },
    );
    if (apiError) return fail(400, { error: apiErrorKey(apiError).key });
    return { checklist: true };
  },

  delete: async (event) => {
    await apiFor(event).DELETE("/api/v1/tasks/{task_id}", {
      params: { path: { task_id: event.params.id } },
    });
    // Back where the detour started (#408); the register only when nothing said otherwise. This
    // is the case the browser-only breadcrumb trail can never serve — a server-side redirect has
    // no `sessionStorage` to read, which is why the origin travels in the URL.
    throw redirect(303, originOf(event.url) ?? "/tasks");
  },

  // Task scheduling (#188) — the same shared helpers the calendar page uses, so the two entry
  // points can't drift. The schedule modal and the block panel post here.
  scheduleTask: async (event) => {
    const result = await createScheduleAction(event);
    return result.error ? fail(400, { error: result.error }) : { scheduled: true };
  },
  updateSchedule: async (event) => {
    const result = await updateScheduleAction(event);
    return result.error ? fail(400, { error: result.error }) : { scheduleUpdated: true };
  },
  deleteSchedule: async (event) => {
    const result = await deleteScheduleAction(event);
    return result.error ? fail(400, { error: result.error }) : { scheduleDeleted: true };
  },
  logScheduleTime: async (event) => {
    const result = await logScheduleTimeAction(event);
    return result.error ? fail(400, { error: result.error }) : { timeLogged: true };
  },

  /** Inline project create from the edit surface's picker (docs/UX.md — per-picker definition
   *  of done). Returns `inlineCreated` so the picker auto-selects the new project. */
  createProject: async (event) => {
    const form = await event.request.formData();
    const name = String(form.get("name") ?? "").trim();
    if (!name) return fail(400, { qcError: "errors.required" });
    // A project belongs to a client (`ProjectCreate`): named here so the dialog says
    // which field, instead of relaying a bare validation envelope.
    const company_id = String(form.get("company_id") ?? "").trim();
    if (!company_id) return fail(400, { qcError: "errors.projects_company_required" });
    const { data, error: apiError } = await apiFor(event).POST("/api/v1/projects", {
      body: {
        name,
        company_id,
        status: "active",
        budget_period: "total",
        currency: event.locals.theme.currency,
        billable_default: true,
        custom: {},
      },
    });
    if (apiError || !data) return fail(400, { qcError: apiErrorKey(apiError).key });
    return { inlineCreated: { slot: "project", id: data.id, name: data.name } };
  },

  createCompany: createCompanyAction,

  // Contactmomenten + Drive panel contracts (the panels post to their host page).
  ...interactionActions,
  ...driveActions,
};
