/**
 * A change between two figures, the way every overview tile and cell states one.
 *
 * The arrow is the direction the number moved and the tone is the verdict — kept apart on
 * purpose (docs/REPORTING.md's `change_badge`): a cost that fell draws a down arrow in green.
 * Here the caller says whether up is good (`lowerIsBetter`), and `null` means there was nothing
 * to compare against, which is drawn as an em-dash rather than as `+100%` over a zero.
 */
import { fmtNumber } from "$lib/core/format";

export interface Delta {
  /** The change as a fraction of the previous figure, in whole percent. */
  pct: number;
  /** "+12%" / "−8%" — the sign always stated, so a tile never reads as a bare figure. */
  text: string;
  /** Which way the figure moved; the arrow. */
  direction: "up" | "down" | "flat";
  /** The verdict the tone carries: `good`, `bad` or `neutral` — the SummaryStrip vocabulary. */
  tone: "good" | "bad" | "neutral";
}

export function delta(
  current: number,
  previous: number | null | undefined,
  { lowerIsBetter = false } = {},
): Delta | null {
  if (previous == null || previous <= 0) return null;
  const pct = Math.round(((current - previous) / previous) * 100);
  const direction = pct > 0 ? "up" : pct < 0 ? "down" : "flat";
  const sign = pct > 0 ? "+" : pct < 0 ? "−" : "";
  const better = direction === "flat" ? null : (direction === "up") !== lowerIsBetter;
  return {
    pct,
    text: `${sign}${fmtNumber(Math.abs(pct), 0)}%`,
    direction,
    tone: better == null ? "neutral" : better ? "good" : "bad",
  };
}

/** The share of a whole, in whole percent — `0` over an empty whole rather than `NaN`. */
export function sharePct(part: number, whole: number): number {
  return whole > 0 ? Math.round((part / whole) * 100) : 0;
}
