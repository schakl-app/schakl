import { fail } from "@sveltejs/kit";
import type { Actions } from "@sveltejs/kit";

import { isoAddDays } from "$lib/core/calendar";
import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";
import { getTimeZone } from "$lib/core/timezone";

/**
 * The availability actions behind {@link AvailabilityManager} — spread into every host route, so
 * the freelancer's own page, the availability overview and the manager's roster drive one surface
 * through one set of writes.
 *
 * `user_id` travels on every form and is simply omitted for "me": the API resolves an absent one
 * to the caller and demands `leave.availability.write:any` for anybody else, so the browser never
 * decides whose week it is writing.
 */

/** How far ahead a host that has no window of its own reads. A year covers a repeating rhythm's
 *  next occurrences, and availability is a handful of rows per person. */
export const AVAILABILITY_WINDOW_DAYS = 365;

/** Today as the tenant's calendar day (§8), never the Node server's UTC one.
 *
 * `new Date().toISOString()` is what this used to do, and between midnight and 02:00 Amsterdam
 * time it names yesterday — so a row created "today" fell outside the window that was supposed to
 * start today. The same fault #316 records for the marketing dashboard's own day arithmetic. */
export function availabilityToday(): string {
  // en-CA formats as YYYY-MM-DD, which is the wire shape the API takes.
  return new Intl.DateTimeFormat("en-CA", { timeZone: getTimeZone() }).format(new Date());
}

export function availabilityWindow(from: string = availabilityToday()): {
  date_from: string;
  date_to: string;
} {
  return { date_from: from, date_to: isoAddDays(from, AVAILABILITY_WINDOW_DAYS) };
}

/**
 * The window a URL asks for, clamped to something an unbounded read can serve.
 *
 * **The view is the URL** (§9): `?from=`/`?to=` is what makes "what was I available for in
 * March" a link you can send, and its absence is what made the past unreachable — every host
 * before this one hardcoded `today → +365` and offered no way back. A missing or unparseable
 * bound falls back rather than 422-ing a page reached from an old bookmark (#316's rule), and a
 * span past two years is clamped rather than refused: the read filters occurrences in Python, so
 * the cost is the person's rows and the window's length together.
 */
export const AVAILABILITY_MAX_SPAN_DAYS = 730;

export function resolveAvailabilityWindow(url: URL): { date_from: string; date_to: string } {
  const iso = (value: string | null): string | null =>
    value && /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(`${value}T00:00:00Z`))
      ? value
      : null;
  const fallback = availabilityWindow();
  const from = iso(url.searchParams.get("from")) ?? fallback.date_from;
  const to = iso(url.searchParams.get("to")) ?? isoAddDays(from, AVAILABILITY_WINDOW_DAYS);
  if (to < from) return { date_from: from, date_to: from };
  const ceiling = isoAddDays(from, AVAILABILITY_MAX_SPAN_DAYS);
  return { date_from: from, date_to: to > ceiling ? ceiling : to };
}

/** `""` → `null`: an empty optional field is "not set", never the empty string. */
function optional(form: FormData, field: string): string | null {
  return String(form.get(field) ?? "").trim() || null;
}

/** A repeat cadence in weeks, or `null` for a one-off. */
function repeatWeeks(form: FormData): number | null {
  const raw = optional(form, "repeat_weeks");
  if (raw === null) return null;
  const weeks = Number(raw);
  return Number.isInteger(weeks) && weeks >= 1 && weeks <= 8 ? weeks : null;
}

export const availabilityActions = {
  /** One extra or unavailable day, optionally repeating. */
  saveAvailability: async (event) => {
    const form = await event.request.formData();
    const date = optional(form, "date");
    const kind = String(form.get("kind") ?? "");
    if (!date || (kind !== "extra" && kind !== "unavailable")) {
      return fail(400, { error: "errors.required" });
    }
    const weeks = repeatWeeks(form);
    const { error } = await apiFor(event).POST("/api/v1/leave/availability", {
      body: {
        user_id: optional(form, "user_id"),
        kind,
        date,
        start_time: optional(form, "start_time"),
        end_time: optional(form, "end_time"),
        repeat_weeks: weeks,
        // A bound with no cadence is a 422 (it would be a limit on a repeat that never happens),
        // so it only travels with one.
        repeat_until: weeks === null ? null : optional(form, "repeat_until"),
        note: optional(form, "note"),
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { availabilitySaved: true };
  },

  /**
   * Correct a row rather than retyping it.
   *
   * Every field is sent on every save, `null` included: the API reads `model_fields_set`, so an
   * omitted key means "leave alone" and an explicit `null` clears — and this form always shows
   * every field, so what the user left empty *is* what they mean to clear. A move's half is
   * edited on its own here; the pair only binds delete, because correcting the replacement day's
   * hours is not a statement about the day that was dropped.
   */
  updateAvailability: async (event) => {
    const form = await event.request.formData();
    const id = optional(form, "id");
    const kind = String(form.get("kind") ?? "");
    if (!id || (kind !== "extra" && kind !== "unavailable")) {
      return fail(400, { error: "errors.required" });
    }
    const weeks = repeatWeeks(form);
    const { error } = await apiFor(event).PATCH("/api/v1/leave/availability/{entry_id}", {
      params: { path: { entry_id: id } },
      body: {
        kind,
        date: optional(form, "date"),
        start_time: optional(form, "start_time"),
        end_time: optional(form, "end_time"),
        repeat_weeks: weeks,
        repeat_until: weeks === null ? null : optional(form, "repeat_until"),
        note: optional(form, "note"),
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { availabilitySaved: true, availabilityUpdated: true };
  },

  /** "Not Tuesday, Thursday instead" — the two rows the API writes as one act. */
  moveAvailability: async (event) => {
    const form = await event.request.formData();
    const from_date = optional(form, "from_date");
    const to_date = optional(form, "to_date");
    if (!from_date || !to_date) return fail(400, { error: "errors.required" });
    const weeks = repeatWeeks(form);
    const { error } = await apiFor(event).POST("/api/v1/leave/availability/move", {
      body: {
        user_id: optional(form, "user_id"),
        from_date,
        to_date,
        start_time: optional(form, "start_time"),
        end_time: optional(form, "end_time"),
        repeat_weeks: weeks,
        repeat_until: weeks === null ? null : optional(form, "repeat_until"),
        note: optional(form, "note"),
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { availabilitySaved: true, availabilityMoved: true };
  },

  /** Removing a move's row removes its other half too — the API owns that rule. */
  deleteAvailability: async (event) => {
    const form = await event.request.formData();
    const id = optional(form, "id");
    if (!id) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).DELETE("/api/v1/leave/availability/{entry_id}", {
      params: { path: { entry_id: id } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { availabilitySaved: true, availabilityDeleted: true };
  },
} satisfies Actions;
