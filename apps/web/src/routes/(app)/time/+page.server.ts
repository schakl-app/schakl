import { fail } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";
import { holidayName } from "$lib/modules/leave/format";

import type { Actions, PageServerLoad } from "./$types";

/** ISO date (YYYY-MM-DD) of the Monday on or before `d`. */
function weekStartOf(iso: string): string {
  const d = new Date(iso + "T00:00:00Z");
  const day = d.getUTCDay(); // 0=Sun..6=Sat
  const diff = (day + 6) % 7; // days since Monday
  d.setUTCDate(d.getUTCDate() - diff);
  return d.toISOString().slice(0, 10);
}

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function parseCustom(raw: FormDataEntryValue | null): Record<string, unknown> {
  try {
    return JSON.parse(String(raw ?? "{}")) as Record<string, unknown>;
  } catch {
    return {};
  }
}

function isoAddDays(iso: string, days: number): string {
  const d = new Date(iso + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/**
 * Own approved leave, per weekday column, from the API's per-day breakdown (#48).
 *
 * It used to spread a request's hours evenly over its Mon–Fri days, which is wrong the moment a
 * schedule exists: a Thursday afternoon plus a Friday morning is 2 h and 5 h, not 3,5 and 3,5.
 * The shape now comes from `TeamLeaveItem.days`, computed once, on the server, from the same
 * schedule and holiday calendar the hours themselves came from.
 */
function leaveHoursForWeek(
  items: { status: string; days: { date: string; hours: number | string }[] }[],
  weekDays: string[],
): number[] {
  const hoursByDay = new Map<string, number>();
  for (const item of items) {
    if (item.status !== "approved") continue;
    for (const day of item.days) {
      hoursByDay.set(day.date, (hoursByDay.get(day.date) ?? 0) + Number(day.hours));
    }
  }
  return weekDays.map((d) => hoursByDay.get(d) ?? 0);
}

export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  const selectedDate = event.url.searchParams.get("date") || todayIso();
  const week_start = event.url.searchParams.get("week") || weekStartOf(selectedDate);
  // Deep link from a client page: ?company= presets the entry form's client, beating the
  // last-used default.
  const presetCompanyId = event.url.searchParams.get("company") ?? "";
  const leaveEnabled = event.locals.theme?.enabledModules?.includes("leave") ?? false;

  // Lookups (companies/projects/tasks/members) come from the /time layout load, which does
  // not rerun on day/week navigation — keep this load down to what actually changes.
  const weekEnd = isoAddDays(week_start, 6);
  const [timer, week, day, recent, leave, holidays] = await Promise.all([
    api.GET("/api/v1/time/timer"),
    api.GET("/api/v1/time/timesheet", { params: { query: { week_start } } }),
    api.GET("/api/v1/time/day", { params: { query: { date: selectedDate } } }),
    // Most recent entry drives the smart defaults (last-used client/project).
    api.GET("/api/v1/time/entries", { params: { query: { limit: 1, offset: 0 } } }),
    // Approved leave overlaps into the timesheet as its own row (§14, no double count).
    leaveEnabled && event.locals.user
      ? api.GET("/api/v1/leave/team", {
          params: {
            query: { date_from: week_start, date_to: weekEnd, user_id: event.locals.user.id },
          },
        })
      : Promise.resolve({ data: null }),
    // One ranged call, not one per year: a timesheet week straddles New Year's Eve (#47).
    leaveEnabled
      ? api.GET("/api/v1/leave/holidays", {
          params: { query: { date_from: week_start, date_to: weekEnd } },
        })
      : Promise.resolve({ data: null }),
  ]);

  const lastEntry = recent.data?.items?.[0] ?? null;
  const weekDays = week.data?.days ?? [];
  const holidayByDate = new Map(
    (holidays.data ?? []).map((h) => [h.date, holidayName(h.name_i18n, event.locals.locale)]),
  );
  return {
    running: timer.data ?? null,
    week: week.data ?? null,
    day: day.data ?? null,
    selectedDate,
    week_start,
    today: todayIso(),
    presetCompanyId,
    lastCompanyId: lastEntry?.company_id ?? "",
    lastProjectId: lastEntry?.project_id ?? "",
    leaveHours: leave.data ? leaveHoursForWeek(leave.data, weekDays) : null,
    holidays: holidays.data ? weekDays.map((d) => holidayByDate.get(d) ?? null) : null,
  };
};

export const actions: Actions = {
  startTimer: async (event) => {
    const form = await event.request.formData();
    await apiFor(event).POST("/api/v1/time/timer/start", {
      body: {
        description: String(form.get("description") ?? "").trim() || null,
        company_id: String(form.get("company_id") ?? "").trim() || null,
        project_id: String(form.get("project_id") ?? "").trim() || null,
        billable: form.get("billable") !== "false",
        entry_type_key: String(form.get("entry_type_key") ?? "").trim() || null,
        break_minutes: 0,
      },
    });
    return { started: true };
  },

  stopTimer: async (event) => {
    await apiFor(event).POST("/api/v1/time/timer/stop", {});
    return { stopped: true };
  },

  createEntry: async (event) => {
    const form = await event.request.formData();
    const date = String(form.get("date") ?? "").trim();
    const start = String(form.get("start") ?? "").trim();
    const end = String(form.get("end") ?? "").trim();
    if (!date || !start || !end) return fail(400, { error: "errors.required" });

    const { error } = await apiFor(event).POST("/api/v1/time/entries", {
      body: {
        // Times are entered + stored as wall-clock (as UTC); the API rolls the end forward a day
        // if it isn't after the start (overnight spans).
        started_at: `${date}T${start}:00Z`,
        ended_at: `${date}T${end}:00Z`,
        break_minutes: Number(form.get("break_minutes") ?? 0) || 0,
        description: String(form.get("description") ?? "").trim() || null,
        company_id: String(form.get("company_id") ?? "").trim() || null,
        project_id: String(form.get("project_id") ?? "").trim() || null,
        task_id: String(form.get("task_id") ?? "").trim() || null,
        subscription_id: String(form.get("subscription_id") ?? "").trim() || null,
        billable: form.get("billable") !== "false",
        entry_type_key: String(form.get("entry_type_key") ?? "").trim() || null,
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { created: true };
  },

  updateEntry: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    const date = String(form.get("date") ?? "").trim();
    const start = String(form.get("start") ?? "").trim();
    const end = String(form.get("end") ?? "").trim();
    if (!id || !date || !start || !end) return fail(400, { error: "errors.required" });

    const { error } = await apiFor(event).PATCH("/api/v1/time/entries/{entry_id}", {
      params: { path: { entry_id: id } },
      body: {
        started_at: `${date}T${start}:00Z`,
        ended_at: `${date}T${end}:00Z`,
        break_minutes: Number(form.get("break_minutes") ?? 0) || 0,
        description: String(form.get("description") ?? "").trim() || null,
        company_id: String(form.get("company_id") ?? "").trim() || null,
        project_id: String(form.get("project_id") ?? "").trim() || null,
        task_id: String(form.get("task_id") ?? "").trim() || null,
        subscription_id: String(form.get("subscription_id") ?? "").trim() || null,
        billable: form.get("billable") !== "false",
        entry_type_key: String(form.get("entry_type_key") ?? "").trim() || null,
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { updated: true };
  },

  // Quick-create from the entry form: log hours for a brand-new client/project without
  // leaving the page. Custom fields are validated by the API against the tenant's
  // definitions; write rights are enforced there too (clients get 403).
  createCompany: async (event) => {
    const form = await event.request.formData();
    const name = String(form.get("name") ?? "").trim();
    if (!name) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).POST("/api/v1/companies", {
      body: {
        name,
        website: String(form.get("website") ?? "").trim() || null,
        status: String(form.get("status") ?? "active") as "active",
        custom: parseCustom(form.get("custom")),
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { companyCreated: true };
  },

  createProject: async (event) => {
    const form = await event.request.formData();
    const name = String(form.get("name") ?? "").trim();
    if (!name) return fail(400, { error: "errors.required" });
    const rate = Number(String(form.get("hourly_rate") ?? "").trim());
    const { error } = await apiFor(event).POST("/api/v1/projects", {
      body: {
        name,
        company_id: String(form.get("company_id") ?? "").trim() || null,
        status: "active",
        budget_period: "total",
        currency: event.locals.theme.currency,
        billable_default: form.get("billable_default") === "on",
        hourly_rate: Number.isFinite(rate) && rate > 0 ? rate : null,
        custom: parseCustom(form.get("custom")),
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { projectCreated: true };
  },

  deleteEntry: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (id) {
      await apiFor(event).DELETE("/api/v1/time/entries/{entry_id}", {
        params: { path: { entry_id: id } },
      });
    }
    return { deleted: true };
  },

  // Personal viewing preference: full 7-day week vs Mon–Fri workweek (saved per user).
  saveView: async (event) => {
    const form = await event.request.formData();
    const week_view = String(form.get("week_view") ?? "full") === "work" ? "work" : "full";
    await apiFor(event).PUT("/api/v1/prefs", {
      body: { prefs: { time: { week_view } } },
    });
    return { viewSaved: true };
  },
};
