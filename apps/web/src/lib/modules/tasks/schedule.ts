/**
 * Task-scheduling helpers shared by the calendar source, the schedule modal and the task
 * panel (#188). The API stores/returns UTC instants; the browser only ever converts *from* an
 * instant *to* the org-local day/time (deterministic and DST-safe), never the other way — that
 * direction is the API's job, so a day-drag stays correct across a DST boundary (§8).
 *
 * `localDayTime` itself lives in `$lib/core/wallclock` now: the time module needed the same
 * conversion and may not import a sibling module's internals (§6), so it moved to core and is
 * re-exported here for the callers that already knew it by this name.
 */
export { localDayTime } from "$lib/core/wallclock";

/** Whole worked minutes between two instants — the block's length, for the log-time prefill. */
export function durationMinutes(startsAt: string, endsAt: string): number {
  return Math.round((new Date(endsAt).getTime() - new Date(startsAt).getTime()) / 60_000);
}
