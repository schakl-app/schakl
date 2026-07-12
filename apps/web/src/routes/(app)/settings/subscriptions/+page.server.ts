import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

function parseIds(raw: FormDataEntryValue | null): string[] {
  try {
    const parsed = JSON.parse(String(raw ?? "[]"));
    return Array.isArray(parsed) ? parsed.filter((v) => typeof v === "string") : [];
  } catch {
    return [];
  }
}

export const load: PageServerLoad = async (event) => {
  const user = event.locals.user;
  if (!can(user, "subscriptions.type.manage") && !can(user, "subscriptions.template.manage")) {
    throw redirect(303, "/");
  }
  const api = apiFor(event);
  const [types, templates, taskTemplates] = await Promise.all([
    api.GET("/api/v1/subscriptions/types", { params: { query: { include_inactive: true } } }),
    api.GET("/api/v1/subscriptions/templates"),
    // For the "spawn on activation" picker; admin-gated screen, so the read grant is there.
    api.GET("/api/v1/tasks/templates"),
  ]);
  return {
    types: types.data ?? [],
    templates: templates.data ?? [],
    taskTemplates: (taskTemplates.data ?? []).map((tpl) => ({ id: tpl.id, name: tpl.name })),
    canManageTypes: can(user, "subscriptions.type.manage"),
    canManageTemplates: can(user, "subscriptions.template.manage"),
    locale: event.locals.locale,
  };
};

export const actions: Actions = {
  saveType: async (event) => {
    const form = await event.request.formData();
    const type_id = String(form.get("id") ?? "");
    const label_i18n = {
      nl: String(form.get("label_nl") ?? "").trim(),
      en: String(form.get("label_en") ?? "").trim(),
    };
    const position = Number(form.get("position") ?? 0) || 0;
    const task_template_ids = parseIds(form.get("task_template_ids"));
    if (!label_i18n.nl || !label_i18n.en) return fail(400, { error: "errors.required" });

    if (type_id) {
      const { error } = await apiFor(event).PATCH("/api/v1/subscriptions/types/{type_id}", {
        params: { path: { type_id } },
        body: { label_i18n, position, task_template_ids },
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
    } else {
      const key = String(form.get("key") ?? "").trim();
      if (!key) return fail(400, { error: "errors.required" });
      const { error } = await apiFor(event).POST("/api/v1/subscriptions/types", {
        body: { key, label_i18n, position, active: true, task_template_ids },
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
    }
    return { saved: true };
  },

  toggleType: async (event) => {
    const form = await event.request.formData();
    const type_id = String(form.get("id") ?? "");
    if (type_id) {
      await apiFor(event).PATCH("/api/v1/subscriptions/types/{type_id}", {
        params: { path: { type_id } },
        body: { active: form.get("active") === "true" },
      });
    }
    return { toggled: true };
  },

  deleteType: async (event) => {
    const form = await event.request.formData();
    const type_id = String(form.get("id") ?? "");
    if (type_id) {
      await apiFor(event).DELETE("/api/v1/subscriptions/types/{type_id}", {
        params: { path: { type_id } },
      });
    }
    return { deleted: true };
  },

  saveTemplate: async (event) => {
    const form = await event.request.formData();
    const template_id = String(form.get("id") ?? "");
    const name = String(form.get("name") ?? "").trim();
    if (!name) return fail(400, { error: "errors.required" });
    const amount = String(form.get("amount") ?? "").trim();
    const included = String(form.get("included_hours") ?? "").trim();
    const notice = String(form.get("notice_period_days") ?? "").trim();
    const body = {
      name,
      subscription_type_id: String(form.get("subscription_type_id") ?? "").trim() || null,
      interval: String(form.get("interval") ?? "monthly") as "monthly",
      interval_count: Number(form.get("interval_count") ?? 1) || 1,
      amount: amount || null,
      included_hours: included || null,
      notice_period_days: notice ? Number(notice) : null,
      notes: String(form.get("notes") ?? "").trim() || null,
      position: Number(form.get("position") ?? 0) || 0,
    };
    if (template_id) {
      const { error } = await apiFor(event).PATCH(
        "/api/v1/subscriptions/templates/{template_id}",
        { params: { path: { template_id } }, body: body as never },
      );
      if (error) return fail(400, { error: apiErrorKey(error).key });
    } else {
      const { error } = await apiFor(event).POST("/api/v1/subscriptions/templates", {
        body: body as never,
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
    }
    return { saved: true };
  },

  deleteTemplate: async (event) => {
    const form = await event.request.formData();
    const template_id = String(form.get("id") ?? "");
    if (template_id) {
      await apiFor(event).DELETE("/api/v1/subscriptions/templates/{template_id}", {
        params: { path: { template_id } },
      });
    }
    return { deleted: true };
  },
};
