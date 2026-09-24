/**
 * Labels and states for a meeting's lifecycle — one place, read by the list, the panel and the
 * detail page, so a status never has two words for it.
 */
import { t } from "$lib/core/i18n";
import type { UiState } from "$lib/core/state";

export function statusLabel(status: string): string {
  return t(`meetings.status.${status}`);
}

export function kindLabel(kind: string): string {
  return t(`meetings.kind.${kind}`);
}

export function sourceLabel(source: string): string {
  return t(`meetings.source.${source}`);
}

/** The same meaning in the fixed state palette (docs/UX.md §1): `ready` is fine (the minutes
 *  are in and editable), `failed` has gone wrong, and the worker's states are in flight. */
export function statusState(status: string): UiState {
  switch (status) {
    case "failed":
      return "late";
    case "ready":
      return "ok";
    case "queued":
    case "transcribing":
    case "summarising":
    case "recording":
      return "soon";
    default:
      return "neutral";
  }
}

/** Is a worker (or the recorder) still holding the row — the states the screen polls through. */
export function inFlight(status: string): boolean {
  return status === "queued" || status === "transcribing" || status === "summarising";
}

/** `0:07`, `12:40`, `1:02:15` — where in the recording something was said. */
export function fmtClock(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "";
  const whole = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const rest = whole % 60;
  const mm = hours ? String(minutes).padStart(2, "0") : String(minutes);
  return `${hours ? `${hours}:` : ""}${mm}:${String(rest).padStart(2, "0")}`;
}

/** A recording's length as people say it: "12 min", "1 u 40 min". */
export function fmtDuration(seconds: number | null | undefined): string {
  if (!seconds) return "";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return t("meetings.duration.minutes", { minutes: String(minutes) });
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest
    ? t("meetings.duration.hours_minutes", { hours: String(hours), minutes: String(rest) })
    : t("meetings.duration.hours", { hours: String(hours) });
}
