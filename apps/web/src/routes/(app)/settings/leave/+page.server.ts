import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { defaultSchedule, type WorkSchedule } from "$lib/modules/leave/schedule";

import type { Actions, PageServerLoad } from "./$types";

/** The editor posts the whole week as one JSON field; the API validates every rule again. */
function parseSchedule(raw: FormDataEntryValue | null): WorkSchedule | null {
  try {
    return JSON.parse(String(raw ?? "")) as WorkSchedule;
  } catch {
    return null;
  }
}

function currentYear(): number {
  return new Date().getUTCFullYear();
}

function parseYear(raw: string | null): number {
  const year = Number(raw);
  return Number.isInteger(year) && year >= 2000 && year <= 2100 ? year : currentYear();
}

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "leave.type.write")) throw redirect(303, "/");
  const api = apiFor(event);
  const year = parseYear(event.url.searchParams.get("year"));

  const [types, members, profiles, entitlements, settings, holidays] = await Promise.all([
    api.GET("/api/v1/leave/types", { params: { query: { include_inactive: true } } }),
    api.GET("/api/v1/members/lookup"),
    api.GET("/api/v1/leave/profiles"),
    api.GET("/api/v1/leave/entitlements", { params: { query: { year } } }),
    api.GET("/api/v1/leave/settings"),
    // Deactivated holidays render nowhere and count nowhere — except here, where they are managed.
    api.GET("/api/v1/leave/holidays", { params: { query: { year, include_inactive: true } } }),
  ]);

  return {
    year,
    currentYear: currentYear(),
    types: types.data ?? [],
    members: members.data ?? [],
    profiles: profiles.data ?? [],
    entitlements: entitlements.data ?? [],
    defaultSchedule: settings.data?.default_schedule ?? defaultSchedule(),
    holidays: holidays.data ?? [],
    locale: event.locals.locale,
  };
};

function typeBody(form: FormData) {
  const weeks = String(form.get("default_weeks") ?? "").trim();
  const carry = String(form.get("carry_over_months") ?? "").trim();
  return {
    label_i18n: {
      nl: String(form.get("label_nl") ?? "").trim(),
      en: String(form.get("label_en") ?? "").trim(),
    },
    color: String(form.get("color") ?? "emerald"),
    paid: form.get("paid") === "on",
    tracks_balance: form.get("tracks_balance") === "on",
    requires_approval: form.get("requires_approval") === "on",
    // Roostervrij/ADV (#65): entitlement is the scheduled−contract gap, not default_weeks.
    accrues_schedule_gap: form.get("accrues_schedule_gap") === "on",
    default_weeks: weeks ? Number(weeks) : null,
    carry_over_months: carry ? Number(carry) : null,
    position: Number(form.get("position") ?? 0) || 0,
  };
}

export const actions: Actions = {
  saveType: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    const body = typeBody(form);
    if (!body.label_i18n.nl || !body.label_i18n.en) return fail(400, { error: "errors.required" });

    const api = apiFor(event);
    if (id) {
      const { error } = await api.PATCH("/api/v1/leave/types/{type_id}", {
        params: { path: { type_id: id } },
        body,
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
      return { typeSaved: true };
    }
    const key = String(form.get("key") ?? "")
      .trim()
      .toLowerCase();
    if (!key) return fail(400, { error: "errors.required" });
    const { error } = await api.POST("/api/v1/leave/types", {
      body: { ...body, key, active: true },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { typeSaved: true };
  },

  toggleType: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (!id) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).PATCH("/api/v1/leave/types/{type_id}", {
      params: { path: { type_id: id } },
      body: { active: form.get("active") === "true" },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { typeSaved: true };
  },

  deleteType: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (!id) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).DELETE("/api/v1/leave/types/{type_id}", {
      params: { path: { type_id: id } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { typeDeleted: true };
  },

  /** The schedule every employee without their own inherits (#46). */
  saveSchedule: async (event) => {
    const schedule = parseSchedule((await event.request.formData()).get("schedule"));
    if (!schedule) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).PUT("/api/v1/leave/settings", {
      body: { default_schedule: schedule },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { scheduleSaved: true };
  },

  // One save per member row: this year's entitlement per tracked type. Contract hours are no
  // longer entered here — they are derived from the person's schedule (Instellingen → Gebruikers).
  saveMember: async (event) => {
    const form = await event.request.formData();
    const userId = String(form.get("user_id") ?? "");
    const year = parseYear(String(form.get("year") ?? ""));
    if (!userId) return fail(400, { error: "errors.required" });

    const api = apiFor(event);
    // Entitlement inputs are posted as ent_<leave_type_id>.
    for (const [name, value] of form.entries()) {
      if (!name.startsWith("ent_")) continue;
      const hours = Number(String(value).trim());
      if (!Number.isFinite(hours) || hours < 0) continue;
      const { error } = await api.PUT("/api/v1/leave/entitlements", {
        body: {
          user_id: userId,
          leave_type_id: name.slice(4),
          year,
          hours,
        },
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
    }
    return { memberSaved: true };
  },

  generate: async (event) => {
    const form = await event.request.formData();
    const year = parseYear(String(form.get("year") ?? ""));
    const { data, error } = await apiFor(event).POST("/api/v1/leave/entitlements/generate", {
      body: { year },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { generated: data?.created ?? 0 };
  },

  // --- holidays (#47) ---------------------------------------------------------------
  saveHoliday: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    const body = {
      date: String(form.get("date") ?? ""),
      name_i18n: {
        nl: String(form.get("name_nl") ?? "").trim(),
        en: String(form.get("name_en") ?? "").trim(),
      },
    };
    if (!body.date || !body.name_i18n.nl) return fail(400, { error: "errors.required" });

    const api = apiFor(event);
    if (id) {
      const { error } = await api.PATCH("/api/v1/leave/holidays/{holiday_id}", {
        params: { path: { holiday_id: id } },
        body,
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
      return { holidaySaved: true };
    }
    const { error } = await api.POST("/api/v1/leave/holidays", { body: { ...body, active: true } });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { holidaySaved: true };
  },

  /** Deactivate, never delete: a holiday the tenant works must stay off across re-imports. */
  toggleHoliday: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (!id) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).PATCH("/api/v1/leave/holidays/{holiday_id}", {
      params: { path: { holiday_id: id } },
      body: { active: form.get("active") === "true" },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { holidaySaved: true };
  },

  deleteHoliday: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (!id) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).DELETE("/api/v1/leave/holidays/{holiday_id}", {
      params: { path: { holiday_id: id } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { holidayDeleted: true };
  },

  importHolidays: async (event) => {
    const form = await event.request.formData();
    const year = parseYear(String(form.get("year") ?? ""));
    const { data, error } = await apiFor(event).POST("/api/v1/leave/holidays/import", {
      body: { year },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { imported: data ?? { created: 0, updated: 0, skipped: 0 } };
  },
};
