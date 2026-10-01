/**
 * Labels, states and money for the Meta ads screens.
 *
 * **Money arrives in cents and is printed in the account's own currency.** The API speaks
 * minor units because Meta does (`daily_budget_cents: 2500`), and an ad account is in whatever
 * currency its owner chose — so this never reaches for the app's `fmtMoney`, which prints the
 * tenant's euro.
 */
import { dateLocale } from "$lib/core/format";
import { hasMessage, t } from "$lib/core/i18n";
import type { UiState } from "$lib/core/state";

export function fmtCents(cents: number | null | undefined, currency: string | null): string {
  if (cents === null || cents === undefined) return "—";
  return fmtAmount(cents / 100, currency);
}

export function fmtAmount(amount: number | null | undefined, currency: string | null): string {
  if (amount === null || amount === undefined) return "—";
  try {
    return new Intl.NumberFormat(dateLocale(), {
      style: "currency",
      currency: currency || "EUR",
    }).format(amount);
  } catch {
    // A currency code the platform does not know prints as a bare number beside its code.
    return `${amount.toFixed(2)} ${currency ?? ""}`.trim();
  }
}

export function fmtCount(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat(dateLocale()).format(value);
}

export function fmtPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${new Intl.NumberFormat(dateLocale(), { maximumFractionDigits: 2 }).format(value)}%`;
}

/** A typed amount ("25", "25,50", "25.5") as cents, or `null` when it is not one. */
export function parseAmount(raw: string): number | null {
  const text = raw.trim().replace(/\s/g, "").replace(",", ".");
  if (!/^\d+(\.\d{1,2})?$/.test(text)) return null;
  const cents = Math.round(Number(text) * 100);
  return cents > 0 ? cents : null;
}

/** Meta's own status vocabulary, in the reader's words where we have them. */
export function statusLabel(status: string | null | undefined): string {
  if (!status) return "—";
  const key = `meta_ads.status.${status.toLowerCase()}`;
  return hasMessage(key) ? t(key) : status.replaceAll("_", " ").toLowerCase();
}

/**
 * The fixed state palette (docs/UX.md §1). Running is `ok`, waiting on Meta's review is
 * `soon`, anything Meta refused or flagged is `late` — and paused is `neutral`, because a
 * paused campaign is a decision, not a problem.
 */
export function statusState(status: string | null | undefined): UiState {
  switch (status) {
    case "ACTIVE":
      return "ok";
    case "PENDING_REVIEW":
    case "IN_PROCESS":
    case "PREAPPROVED":
    case "PENDING_BILLING_INFO":
      return "soon";
    case "DISAPPROVED":
    case "WITH_ISSUES":
      return "late";
    default:
      return "neutral";
  }
}

export function objectiveLabel(objective: string | null | undefined): string {
  if (!objective) return "—";
  const key = `meta_ads.objective.${objective.toLowerCase()}`;
  return hasMessage(key) ? t(key) : objective.replace("OUTCOME_", "").toLowerCase();
}

export function decisionLabel(decision: string, parts: { subject: string; name: string }): string {
  return t(`meta_ads.decision.${decision}`, parts);
}

export function subjectLabel(subject: string): string {
  return t(`meta_ads.subject.${subject}`);
}

/** Meta's `account_status`, for the ones worth naming. */
export function accountStatusLabel(status: number | null | undefined): string | null {
  if (status === null || status === undefined || status === 1) return null;
  const key = `meta_ads.account_status.${status}`;
  return hasMessage(key) ? t(key) : t("meta_ads.account_status.other", { code: String(status) });
}

export const PERIODS = ["7d", "30d", "month", "last_month", "90d"] as const;
