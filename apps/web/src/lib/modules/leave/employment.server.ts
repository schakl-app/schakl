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

/** `derive` = the norm shortfall, `roster` = already in the week, `custom` = an agreed figure. */
function freeTimeHours(mode: string, raw: FormDataEntryValue | null): string | null {
  if (mode === "roster") return "0";
  if (mode !== "custom") return null;
  return (
    String(raw ?? "")
      .trim()
      .replace(",", ".") || null
  );
}

export const employmentActions = {
  /**
   * The whole employment arrangement in **one** save: the contract period, the week it is worked,
   * how much free time it accrues, and optionally the pattern that places those free days.
   *
   * One action rather than the three the three old modals posted, because they were never three
   * decisions — the contract hours only mean something against the week, and the free days only
   * exist because the two differ. docs/UX.md: one save button per editing surface.
   *
   * `contract_id` present = adjust the arrangement in force; absent = a new period (a raise, a
   * new hire). The API refuses an overlapping period either way, so "new" cannot silently
   * rewrite history.
   */
  saveEmployment: async (event) => {
    const form = await event.request.formData();
    const userId = String(form.get("user_id") ?? "");
    if (!userId) return fail(400, { error: "errors.required" });
    const api = apiFor(event);

    const inherit = form.get("inherit") === "true";
    const schedule = inherit ? null : parseSchedule(form.get("schedule"));
    if (!inherit && !schedule) return fail(400, { error: "errors.required" });
    const freeTimeMode = String(form.get("free_time_mode") ?? "derive");
    const free_time_hours_per_week = freeTimeHours(freeTimeMode, form.get("free_time_hours"));
    if (freeTimeMode === "custom" && free_time_hours_per_week === null) {
      // An emptied custom figure would post `null`, which the API reads as "derive the norm
      // shortfall" — the exact opposite of the 0 the wizard's own preview just showed for it.
      return fail(400, { error: "errors.required" });
    }

    const contractId = String(form.get("contract_id") ?? "").trim();
    if (contractId) {
      const { error } = await api.PATCH("/api/v1/leave/contracts/{contract_id}", {
        params: { path: { contract_id: contractId } },
        body: { schedule, free_time_hours_per_week },
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
    } else {
      const start = String(form.get("start_date") ?? "");
      const hours = String(form.get("contract_hours_per_week") ?? "")
        .trim()
        .replace(",", ".");
      if (!start || !hours) return fail(400, { error: "errors.required" });
      const { error } = await api.POST("/api/v1/leave/contracts", {
        body: {
          user_id: userId,
          start_date: start,
          end_date: String(form.get("end_date") ?? "").trim() || null,
          contract_hours_per_week: hours,
          schedule,
          free_time_hours_per_week,
          note: String(form.get("note") ?? "").trim() || null,
        },
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
    }

    // The pattern is optional: plenty of arrangements accrue free time that the employee books
    // by hand. A failure here is reported *with* `employmentSaved`, so the wizard does not offer
    // to re-save a contract that already landed — and the receipt states the failure instead of
    // a bare green "saved" over a pattern that never landed.
    let generated = 0;
    let patternError: string | null = null;
    const patternMode = String(form.get("pattern_mode") ?? "none");
    const typeId = String(form.get("leave_type_id") ?? "");
    const anchor = String(form.get("anchor_date") ?? "");
    if ((patternMode === "spread" || patternMode === "interval") && typeId && anchor) {
      let days_per_year: number | null = null;
      if (patternMode === "spread") {
        const parsed = Number(
          String(form.get("days_per_year") ?? "")
            .trim()
            .replace(",", "."),
        );
        // Falling through with an emptied/invalid count would post {interval_weeks: 1,
        // days_per_year: null} — an every-week pattern that front-loads the whole pot into
        // consecutive weeks. A day count is a whole number or it is an error.
        if (!Number.isInteger(parsed) || parsed <= 0) {
          patternError = "errors.leave_pattern_days_invalid";
        } else {
          days_per_year = parsed;
        }
      }
      if (patternError === null) {
        const { data, error } = await api.POST("/api/v1/leave/recurring", {
          body: {
            user_id: userId,
            leave_type_id: typeId,
            anchor_date: anchor,
            // `interval_weeks` always travels: in spread mode the API overwrites it with the
            // nearest equivalent cadence anyway, and the schema requires it.
            interval_weeks: Number(form.get("interval_weeks") ?? 1) || 1,
            days_per_year,
            start_time: String(form.get("start_time") ?? "").trim() || null,
            end_time: String(form.get("end_time") ?? "").trim() || null,
          },
        });
        if (error) {
          patternError = apiErrorKey(error).key;
        } else {
          generated = data?.generated ?? 0;
        }
      }
    }

    // What the save actually produced — days placed, and days a reprorated pot no longer covers
    // (#264 moves the entitlement and leaves the calendar alone). Reported, never auto-cancelled.
    // Fetched on the failure path too, so the receipt's figures show that nothing was placed.
    const { data: freeTime } = await api.GET("/api/v1/leave/free-time", {
      params: { query: { year: new Date().getFullYear(), user_id: userId } },
    });
    if (patternError !== null) {
      return fail(400, {
        error: patternError,
        employmentSaved: true,
        employmentGenerated: 0,
        freeTime: freeTime ?? null,
      });
    }
    return {
      employmentSaved: true,
      employmentGenerated: generated,
      freeTime: freeTime ?? null,
    };
  },

  /** Give back free days the pot no longer covers — explicit ids the wizard just listed. */
  withdrawFreeTime: async (event) => {
    const form = await event.request.formData();
    const userId = String(form.get("user_id") ?? "");
    const ids = String(form.get("request_ids") ?? "")
      .split(",")
      .filter(Boolean);
    if (ids.length === 0) return fail(400, { error: "errors.required" });
    const api = apiFor(event);
    const { data, error } = await api.POST("/api/v1/leave/free-time/withdraw", {
      body: { request_ids: ids },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    // `employmentSaved` keeps the wizard's receipt mounted (EmploymentModals keys it on that
    // flag — without it the whole wizard remounts at step 1 and the confirmation is
    // unreachable), and the refreshed overview shows the overhang actually emptied.
    const { data: freeTime } = userId
      ? await api.GET("/api/v1/leave/free-time", {
          params: { query: { year: new Date().getFullYear(), user_id: userId } },
        })
      : { data: null };
    return {
      employmentSaved: true,
      employmentGenerated: 0,
      withdrawn: data?.cancelled ?? 0,
      withdrawSkipped: data?.skipped?.length ?? 0,
      freeTime: freeTime ?? null,
    };
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

  /** A recurring free-day pattern (#107): saved, and its days placed right away. */
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

  /**
   * Delete a pattern, and — when the confirmation's checkbox says so — take back the free days it
   * already placed. Without that option "delete" left a year of free Fridays on the calendar with
   * nothing pointing at them and no way out but cancelling each by hand.
   */
  deleteRecurring: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (!id) return { recurringSaved: true, recurringAdded: false, recurringGenerated: 0 };
    const { data, error } = await apiFor(event).DELETE("/api/v1/leave/recurring/{recurring_id}", {
      params: {
        path: { recurring_id: id },
        query: { withdraw_days: form.get("withdraw_days") === "true" },
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return {
      recurringSaved: true,
      recurringAdded: false,
      recurringGenerated: 0,
      patternDeleted: true,
      withdrawn: data?.withdrawn ?? 0,
    };
  },
} satisfies Actions;
