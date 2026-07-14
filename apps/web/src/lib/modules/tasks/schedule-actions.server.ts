/**
 * Server-side task-scheduling form actions (#188), shared by the calendar page and the task
 * detail page so the two entry points can never drift. Each reads its own form fields and goes
 * through the typed API client (Golden Rule 6); the API is the authority on the block.
 */
import type { RequestEvent } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

type Result = { error?: string };

/** Create a block. `user_id` omitted → the task's assignee (resolved server-side). */
export async function createScheduleAction(event: RequestEvent): Promise<Result> {
  const form = await event.request.formData();
  const taskId = String(form.get("task_id") ?? "");
  const day = String(form.get("day") ?? "");
  const startTime = String(form.get("start_time") ?? "");
  const durationMinutes = Number(form.get("duration_minutes") ?? 0);
  if (!taskId || !day || !startTime || !durationMinutes) return { error: "errors.required" };
  const userId = String(form.get("user_id") ?? "");
  const note = String(form.get("note") ?? "");
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
  const durationMinutes = Number(form.get("duration_minutes") ?? 0);
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
  const rawMinutes = String(form.get("minutes") ?? "");
  const description = String(form.get("description") ?? "");
  const { error } = await apiFor(event).POST("/api/v1/tasks/schedules/{schedule_id}/log-time", {
    params: { path: { schedule_id: scheduleId } },
    body: {
      minutes: rawMinutes ? Number(rawMinutes) : null,
      break_minutes: Number(form.get("break_minutes") ?? 0),
      description: description || null,
      billable: form.get("billable") !== "false",
    },
  });
  return error ? { error: apiErrorKey(error).key } : {};
}
