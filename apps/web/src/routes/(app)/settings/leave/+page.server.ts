import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

function currentYear(): number {
  return new Date().getUTCFullYear();
}

function parseYear(raw: string | null): number {
  const year = Number(raw);
  return Number.isInteger(year) && year >= 2000 && year <= 2100 ? year : currentYear();
}

export const load: PageServerLoad = async (event) => {
  if (!event.locals.user?.canManage) throw redirect(303, "/");
  const api = apiFor(event);
  const year = parseYear(event.url.searchParams.get("year"));

  const [types, members, profiles, entitlements] = await Promise.all([
    api.GET("/api/v1/leave/types", { params: { query: { include_inactive: true } } }),
    api.GET("/api/v1/members/lookup"),
    api.GET("/api/v1/leave/profiles"),
    api.GET("/api/v1/leave/entitlements", { params: { query: { year } } }),
  ]);

  return {
    year,
    currentYear: currentYear(),
    types: types.data ?? [],
    members: members.data ?? [],
    profiles: profiles.data ?? [],
    entitlements: entitlements.data ?? [],
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

  // One save per member row: contract hours + this year's entitlement per tracked type.
  saveMember: async (event) => {
    const form = await event.request.formData();
    const userId = String(form.get("user_id") ?? "");
    const year = parseYear(String(form.get("year") ?? ""));
    const hoursPerWeek = Number(form.get("hours_per_week") ?? 0);
    if (!userId || !hoursPerWeek) return fail(400, { error: "errors.required" });

    const api = apiFor(event);
    const profile = await api.PUT("/api/v1/leave/profiles/{user_id}", {
      params: { path: { user_id: userId } },
      body: { hours_per_week: hoursPerWeek },
    });
    if (profile.error) return fail(400, { error: apiErrorKey(profile.error).key });

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
};
