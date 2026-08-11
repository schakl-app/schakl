import { fail } from "@sveltejs/kit";
import type { Actions } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

/**
 * The availability actions behind {@link AvailabilityManager} — spread into every host route, so
 * the freelancer's own page and the manager's roster drive one surface through one set of writes.
 *
 * `user_id` travels on every form and is simply omitted for "me": the API resolves an absent one
 * to the caller and demands `leave.availability.write:any` for anybody else, so the browser never
 * decides whose week it is writing.
 */

/** How far ahead the manager and the employee both read. A year covers a repeating rhythm's
 *  next occurrences without paging, and availability is a handful of rows per person. */
export const AVAILABILITY_WINDOW_DAYS = 365;

export function availabilityWindow(from: Date = new Date()): {
  date_from: string;
  date_to: string;
} {
  const to = new Date(from);
  to.setDate(to.getDate() + AVAILABILITY_WINDOW_DAYS);
  return { date_from: from.toISOString().slice(0, 10), date_to: to.toISOString().slice(0, 10) };
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
