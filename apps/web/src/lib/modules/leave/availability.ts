/**
 * What an availability row *reads* as — shared by the compact list on a freelancer's own page and
 * by the overview table, so the two can never describe the same Thursday differently.
 *
 * The pairing rule is the reason this is not two copies: a move is two rows sharing a `pair_id`
 * (CLAUDE.md §14) and every surface has to fold them back into the one act the user performed.
 * A surface that forgot would draw "not Tuesday" and "extra Saturday" as two unrelated lines and
 * then offer to delete one of them, which the API refuses to do by halves.
 */
import { fmtClockTime, fmtNumericDate, RANGE_DASH } from "$lib/core/format";
import { t } from "$lib/core/i18n";

export interface AvailabilityEntry {
  id: string;
  user_id: string;
  /** The live account name, resolved by the API (`AvailabilityRead.user_name`); `""` on the
   *  single-row responses, where the caller already knows whose row they wrote. */
  user_name?: string;
  kind: string;
  date: string;
  start_time?: string | null;
  end_time?: string | null;
  repeat_weeks?: number | null;
  repeat_until?: string | null;
  /** Shared by the two halves of a move; `null` on a standalone row. */
  pair_id?: string | null;
  note?: string | null;
}

/** One line on a screen: a standalone row, or the two halves of a move drawn as the single act
 *  they were. `primary` is the row every control acts on — for a move, the day being *added*,
 *  because that is the half carrying the times. */
export type AvailabilityRow =
  | { kind: "move"; from: AvailabilityEntry; to: AvailabilityEntry; primary: AvailabilityEntry }
  | { kind: "single"; entry: AvailabilityEntry; primary: AvailabilityEntry };

/** Fold a flat list of rows into the acts behind them, oldest day first. */
export function availabilityRows(entries: AvailabilityEntry[]): AvailabilityRow[] {
  const byPair: Record<string, AvailabilityEntry[]> = {};
  const singles: AvailabilityEntry[] = [];
  for (const entry of entries) {
    if (entry.pair_id) (byPair[entry.pair_id] ??= []).push(entry);
    else singles.push(entry);
  }
  const moves = Object.values(byPair)
    .map((group) => ({
      from: group.find((e) => e.kind === "unavailable") ?? group[0],
      to: group.find((e) => e.kind === "extra") ?? group[0],
    }))
    // A pair whose halves were somehow separated is not a move any more; the surviving row is
    // shown on its own rather than as half a swap nobody made.
    .filter((m) => m.from !== m.to);
  const paired = new Set(moves.flatMap((m) => [m.from.id, m.to.id]));
  const orphans = entries.filter((e) => e.pair_id && !paired.has(e.id));
  const rows: AvailabilityRow[] = [
    ...moves.map((m) => ({ kind: "move" as const, ...m, primary: m.to })),
    ...[...singles, ...orphans].map((entry) => ({
      kind: "single" as const,
      entry,
      primary: entry,
    })),
  ];
  return rows.sort((a, b) => sortKey(a).localeCompare(sortKey(b)));
}

function sortKey(row: AvailabilityRow): string {
  return row.kind === "move" ? row.from.date : row.entry.date;
}

/** The id a deep link resolves to, as the id its row is drawn under.
 *
 *  A calendar chip names one half of a move while the list draws the pair as *one* line, so the
 *  id in the URL is often not the id the row is keyed by — resolving it is what stops a link to
 *  the dropped Tuesday finding nothing. `""` when the link names no row this surface holds. */
export function availabilityRowId(rows: AvailabilityRow[], entryId: string): string {
  if (!entryId) return "";
  for (const row of rows) {
    const ids = row.kind === "move" ? [row.from.id, row.to.id] : [row.entry.id];
    if (ids.includes(entryId)) return row.primary.id;
  }
  return "";
}

/** The window in words. A one-sided bound gets a phrase, not a dangling dash: an omitted bound
 *  *means* the day's own start or end (#48), and "13:00–" reads as a truncation, not a rule. */
export function availabilityWindowText(entry: AvailabilityEntry): string {
  if (!entry.start_time && !entry.end_time) return t("leave.availability.whole_day");
  if (entry.start_time && entry.end_time) {
    return `${fmtClockTime(entry.start_time)}${RANGE_DASH}${fmtClockTime(entry.end_time)}`;
  }
  return entry.start_time
    ? t("leave.availability.from_time", { time: fmtClockTime(entry.start_time) })
    : t("leave.availability.until_time", { time: fmtClockTime(entry.end_time ?? "") });
}

/** "Every other week until 12-06-2026", or `null` for a one-off. */
export function availabilityRepeatText(entry: AvailabilityEntry): string | null {
  if (!entry.repeat_weeks) return null;
  const cadence =
    entry.repeat_weeks === 1
      ? t("leave.recurring.every_week")
      : t("leave.recurring.every_n", { n: entry.repeat_weeks });
  return entry.repeat_until
    ? `${cadence} ${t("leave.availability.until", { date: fmtNumericDate(entry.repeat_until) })}`
    : cadence;
}

/** What kind of statement this line is: an extra day, a dropped one, or a swap. */
export function availabilityKindText(row: AvailabilityRow): string {
  if (row.kind === "move") return t("leave.availability.moved");
  return row.entry.kind === "extra"
    ? t("leave.availability.extra")
    : t("leave.availability.unavailable");
}
