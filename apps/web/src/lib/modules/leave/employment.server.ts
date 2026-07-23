import { fail } from "@sveltejs/kit";
import type { Actions } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { WorkSchedule } from "./schedule";

/**
 * The employment-data actions (work schedule, contract, recurring free days, hourly rate) that
 * back the shared {@link EmploymentModals} kebab. Declared once here and spread into every host
 * route's `actions` — Instellingen → Gebruikers and the team leave roster — so the two surfaces
 * can never drift, exactly like the shared components they drive. Each is a thin proxy through
 * the tenant-scoped API, which re-checks the permission (`leave.profile.manage` / `leave.rate.*`)
 * and every rule; the browser never sets `hours` and cannot cross a scope the key lacks.
 */

/** The editor posts the whole week as one JSON field; the API validates every rule again. */
function parseSchedule(raw: FormDataEntryValue | null): WorkSchedule | null {
  try {
    return JSON.parse(String(raw ?? "")) as WorkSchedule;
  } catch {
    return null;
  }
}

export const employmentActions = {
  /**
   * This person's week (#46), or `null` to follow the org default. `hours_per_week` is derived
   * from it by the API and never posted from here.
   */
  saveSchedule: async (event) => {
    const form = await event.request.formData();
    const userId = String(form.get("user_id") ?? "");
    if (!userId) return fail(400, { error: "errors.required" });
    const inherit = form.get("inherit") === "true";
    const schedule = inherit ? null : parseSchedule(form.get("schedule"));
    if (!inherit && !schedule) return fail(400, { error: "errors.required" });

    const { error } = await apiFor(event).PUT("/api/v1/leave/profiles/{user_id}", {
      params: { path: { user_id: userId } },
      body: { schedule },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { scheduleSaved: true };
  },

  /**
   * This person's hourly rate (#82), or `null` to clear it. Admin-only (`leave.rate.write`),
   * which the API re-enforces; the empty field means "no rate recorded".
   */
  saveRate: async (event) => {
    const form = await event.request.formData();
    const userId = String(form.get("user_id") ?? "");
    if (!userId) return fail(400, { error: "errors.required" });
    const raw = String(form.get("hourly_rate") ?? "")
      .trim()
      .replace(",", ".");
    const hourly_rate = raw === "" ? null : raw;
    const { error } = await apiFor(event).PUT("/api/v1/leave/rate/{user_id}", {
      params: { path: { user_id: userId } },
      body: { hourly_rate },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { rateSaved: true };
  },

  /** A new employment contract (#65). A *changed* contract is a new row, never an in-place edit. */
  saveContract: async (event) => {
    const form = await event.request.formData();
    const userId = String(form.get("user_id") ?? "");
    const start = String(form.get("start_date") ?? "");
    const hoursRaw = String(form.get("contract_hours_per_week") ?? "")
      .trim()
      .replace(",", ".");
    if (!userId || !start || !hoursRaw) return fail(400, { error: "errors.required" });
    const endRaw = String(form.get("end_date") ?? "").trim();
    const { error } = await apiFor(event).POST("/api/v1/leave/contracts", {
      body: {
        user_id: userId,
        start_date: start,
        end_date: endRaw || null,
        contract_hours_per_week: hoursRaw,
        note: String(form.get("note") ?? "").trim() || null,
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { contractSaved: true };
  },

  /** Terminate a contract by setting its end date (the row survives — it's history). */
  terminateContract: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("contract_id") ?? "");
    const end = String(form.get("end_date") ?? "");
    if (!id || !end) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).PATCH("/api/v1/leave/contracts/{contract_id}", {
      params: { path: { contract_id: id } },
      body: { end_date: end },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { contractSaved: true };
  },

  deleteContract: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("contract_id") ?? "");
    if (id) {
      const { error } = await apiFor(event).DELETE("/api/v1/leave/contracts/{contract_id}", {
        params: { path: { contract_id: id } },
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
    }
    return { contractSaved: true };
  },

  /** A recurring rostered-free-day pattern (#107): saved, and its days placed right away. */
  saveRecurring: async (event) => {
    const form = await event.request.formData();
    const userId = String(form.get("user_id") ?? "");
    const typeId = String(form.get("leave_type_id") ?? "");
    const anchor = String(form.get("anchor_date") ?? "");
    const interval = Number(form.get("interval_weeks") ?? 0);
    if (!userId || !typeId || !anchor || !interval) {
      return fail(400, { error: "errors.required" });
    }
    const { data, error } = await apiFor(event).POST("/api/v1/leave/recurring", {
      body: {
        user_id: userId,
        leave_type_id: typeId,
        anchor_date: anchor,
        interval_weeks: interval,
        // Part-day pattern ("off from 15:00") — absent fields mean the whole scheduled day.
        start_time: String(form.get("start_time") ?? "").trim() || null,
        end_time: String(form.get("end_time") ?? "").trim() || null,
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    // `recurringAdded` separates the add from the toggle/delete: the add closes the modal
    // (#271), so its confirmation is the page's to render, not the modal's.
    return { recurringSaved: true, recurringAdded: true, recurringGenerated: data?.generated ?? 0 };
  },

  /** Deactivating stops future generation; the days already placed stay. */
  toggleRecurring: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (!id) return fail(400, { error: "errors.required" });
    const { data, error } = await apiFor(event).PATCH("/api/v1/leave/recurring/{recurring_id}", {
      params: { path: { recurring_id: id } },
      body: { active: form.get("active") === "true" },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return {
      recurringSaved: true,
      recurringAdded: false,
      recurringGenerated: data?.generated ?? 0,
    };
  },

  deleteRecurring: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (id) {
      const { error } = await apiFor(event).DELETE("/api/v1/leave/recurring/{recurring_id}", {
        params: { path: { recurring_id: id } },
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
    }
    return { recurringSaved: true, recurringAdded: false, recurringGenerated: 0 };
  },
} satisfies Actions;
