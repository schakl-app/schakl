/** Display helpers for reporting (issue #300) — labels, badges and the one date format. */
import { RANGE_DASH } from "$lib/core/format";
import { t } from "$lib/core/i18n";
import type { UiState } from "$lib/core/state";

import type { ReportCadence, ReportDelivery } from "./types";

/** Status → the Tailwind classes its pill wears. Colour follows meaning, never rank. */
const STATUS_CLASS: Record<string, string> = {
  draft: "bg-surface text-text-muted",
  generating: "bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300",
  ready: "bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
  sent: "bg-green-50 text-green-700 dark:bg-green-950 dark:text-green-300",
  failed: "bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300",
};

export function statusClass(status: string): string {
  return STATUS_CLASS[status] ?? STATUS_CLASS.draft;
}

export function statusLabel(status: string): string {
  return t(`reporting.status.${status}`);
}

/**
 * The same meaning in the fixed state palette (`docs/UX.md` §1), for the surfaces that draw a
 * row through `PanelRow`: `ready` is the one a person must act on, `failed` has already gone
 * wrong, `sent` is actively fine, and the rest are history or in flight.
 */
export function statusState(status: string): UiState {
  switch (status) {
    case "ready":
      return "today";
    case "failed":
      return "late";
    case "sent":
      return "ok";
    case "generating":
      return "soon";
    default:
      return "neutral";
  }
}

export function audienceLabel(audience: string): string {
  return t(`reporting.audience.${audience}`);
}

export function cadenceLabel(cadence: ReportCadence | string | null | undefined): string {
  return t(`reporting.cadence.${cadence ?? "monthly"}`);
}

export function deliveryLabel(delivery: ReportDelivery | string | null | undefined): string {
  return t(`reporting.delivery.${delivery ?? "review"}`);
}

/**
 * `ready` is the state a person has to act on, so it is the one the list leads with.
 * `generating` is transient and `failed` needs a retry — both are worth a badge; `sent` and
 * `draft` are just history.
 */
export function needsAttention(status: string): boolean {
  return status === "ready" || status === "failed";
}

/** A European date, in the viewer's locale — the app's own convention (docs/UX.md). */
export function fmtDate(value: string | null | undefined, locale: string): string {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "";
  return parsed.toLocaleDateString(locale, {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

/**
 * The period a report covers, as one label.
 *
 * Prefers the snapshot's own label, which was written in the *document's* language when the
 * report was generated — a German client's report says "Juli 2026" on every screen, including a
 * Dutch colleague's. Falls back to the dates only when there is no snapshot yet.
 */
export function periodLabel(
  report: {
    data_snapshot?: Record<string, unknown> | null;
    period_start: string;
    period_end: string;
  },
  locale: string,
): string {
  const snapshot = report.data_snapshot as { period?: { label?: string } } | null | undefined;
  const stored = snapshot?.period?.label;
  if (stored) return stored;
  return `${fmtDate(report.period_start, locale)} ${RANGE_DASH} ${fmtDate(report.period_end, locale)}`;
}

/** A warning code + its detail, as one readable line for the review screen. */
export function warningText(warning: { code: string; detail?: string | null }): string {
  const base = t(warning.code);
  return warning.detail ? `${base} (${warning.detail})` : base;
}
