/** The subscription-catalog management actions (types + templates, issue #142), shared by
 * Instellingen → Abonnementen and the subscriptions page's own beheer sections — one
 * implementation, two surfaces. Tenant labels are optional per locale (docs/UX.md): one
 * language is enough, a missing one falls back at render time. */
import { fail, type RequestEvent } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

function parseIds(raw: FormDataEntryValue | null): string[] {
  try {
    const parsed = JSON.parse(String(raw ?? "[]"));
    return Array.isArray(parsed) ? parsed.filter((v) => typeof v === "string") : [];
  } catch {
    return [];
  }
}

/** Only the filled locales — an empty string stored would shadow the render-time fallback. */
export function parseLabelI18n(form: FormData): Record<string, string> {
  const entries = (["nl", "en"] as const)
    .map((locale) => [locale, String(form.get(`label_${locale}`) ?? "").trim()] as const)
    .filter(([, value]) => value);
  return Object.fromEntries(entries);
}

export const manageActions = {
  saveType: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const type_id = String(form.get("id") ?? "");
    const label_i18n = parseLabelI18n(form);
    const position = Number(form.get("position") ?? 0) || 0;
    const task_template_ids = parseIds(form.get("task_template_ids"));
    if (Object.keys(label_i18n).length === 0) return fail(400, { error: "errors.required" });

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

  toggleType: async (event: RequestEvent) => {
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

  deleteType: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const type_id = String(form.get("id") ?? "");
    if (type_id) {
      await apiFor(event).DELETE("/api/v1/subscriptions/types/{type_id}", {
        params: { path: { type_id } },
      });
    }
    return { deleted: true };
  },

  saveTemplate: async (event: RequestEvent) => {
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
      const { error } = await apiFor(event).PATCH("/api/v1/subscriptions/templates/{template_id}", {
        params: { path: { template_id } },
        body: body as never,
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
    } else {
      const { error } = await apiFor(event).POST("/api/v1/subscriptions/templates", {
        body: body as never,
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
    }
    // `templateSaved` keeps the subscriptions page's "opgeslagen als sjabloon" notice fed.
    return { saved: true, templateSaved: true };
  },

  deleteTemplate: async (event: RequestEvent) => {
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
