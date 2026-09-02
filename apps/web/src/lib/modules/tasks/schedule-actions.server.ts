/**
 * Server-side task-scheduling form actions (#188), shared by the calendar page and the task
 * detail page so the two entry points can never drift. Each reads its own form fields and goes
 * through the typed API client (Golden Rule 6); the API is the authority on the block.
 */
import type { RequestEvent } from "@sveltejs/kit";

import { parsePostedMinutes } from "$lib/core/duration";
import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

type Result = { error?: string };

/**
 * Create a block — or one per person. The modal posts the people it is for as `user_ids`
 * (one field per chip), and the batch route writes them together or not at all; a form that
 * still posts a single `user_id` (or nothing, → the task's assignee, resolved server-side) takes
 * the single route, so the two never disagree about what one block means.
 */
export async function createScheduleAction(event: RequestEvent): Promise<Result> {
  const form = await event.request.formData();
  const taskId = String(form.get("task_id") ?? "");
  const day = String(form.get("day") ?? "");
  const startTime = String(form.get("start_time") ?? "");
  // A duration arrives as the text that was typed ("1:30"), so the parser — not the browser —
  // decides what it means (#326); a bare number still reads as minutes.
  const durationMinutes = parsePostedMinutes(form.get("duration_minutes"));
  if (!taskId || !day || !startTime || !durationMinutes) return { error: "errors.required" };
  const note = String(form.get("note") ?? "");
  const userIds = [...new Set(form.getAll("user_ids").map((value) => String(value).trim()))].filter(
    Boolean,
  );
  if (form.has("user_ids") && userIds.length === 0) return { error: "tasks.schedule.nobody" };
  if (userIds.length > 0) {
    const { error } = await apiFor(event).POST("/api/v1/tasks/schedules/batch", {
      body: {
        task_id: taskId,
        user_ids: userIds,
        day,
        start_time: startTime,
        duration_minutes: durationMinutes,
        note: note || null,
      },
    });
    return error ? { error: apiErrorKey(error).key } : {};
  }
  const userId = String(form.get("user_id") ?? "");
  const { error } = await apiFor(event).POST("/api/v1/tasks/schedules", {
    body: {
      task_id: taskId,
      user_id: userId || null,
      day,
      start_time: startTime,
      duration_minutes: durationMinutes,
      note: note || null,
    },
  });
  return error ? { error: apiErrorKey(error).key } : {};
}

/** Edit / move a block. Sends the full new local values; the API recomputes the instants. */
export async function updateScheduleAction(event: RequestEvent): Promise<Result> {
  const form = await event.request.formData();
  const scheduleId = String(form.get("schedule_id") ?? "");
  const day = String(form.get("day") ?? "");
  const startTime = String(form.get("start_time") ?? "");
  const durationMinutes = parsePostedMinutes(form.get("duration_minutes"));
  if (!scheduleId || !day || !startTime || !durationMinutes) return { error: "errors.required" };
  const userId = String(form.get("user_id") ?? "");
  const note = String(form.get("note") ?? "");
  const { error } = await apiFor(event).PATCH("/api/v1/tasks/schedules/{schedule_id}", {
    params: { path: { schedule_id: scheduleId } },
    body: {
      user_id: userId || null,
      day,
      start_time: startTime,
      duration_minutes: durationMinutes,
      note: note || null,
    },
  });
  return error ? { error: apiErrorKey(error).key } : {};
}

export async function deleteScheduleAction(event: RequestEvent): Promise<Result> {
  const form = await event.request.formData();
  const scheduleId = String(form.get("schedule_id") ?? "");
  if (!scheduleId) return { error: "errors.required" };
  const { error } = await apiFor(event).DELETE("/api/v1/tasks/schedules/{schedule_id}", {
    params: { path: { schedule_id: scheduleId } },
  });
  return error ? { error: apiErrorKey(error).key } : {};
}

/** Confirm-to-log a passed block as a real time entry; everything defaults from the block. */
export async function logScheduleTimeAction(event: RequestEvent): Promise<Result> {
  const form = await event.request.formData();
  const scheduleId = String(form.get("schedule_id") ?? "");
  if (!scheduleId) return { error: "errors.required" };
  const description = String(form.get("description") ?? "");
  const { error } = await apiFor(event).POST("/api/v1/tasks/schedules/{schedule_id}/log-time", {
    params: { path: { schedule_id: scheduleId } },
    body: {
      // null → the block's own duration stands (the API's default).
      minutes: parsePostedMinutes(form.get("minutes")),
      break_minutes: parsePostedMinutes(form.get("break_minutes")) ?? 0,
      description: description || null,
      billable: form.get("billable") !== "false",
    },
  });
  return error ? { error: apiErrorKey(error).key } : {};
}
