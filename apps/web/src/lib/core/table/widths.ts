/**
 * How a fixed-layout grid divides the width it has — the arithmetic behind `DataTable`.
 *
 * Pure, and in its own module rather than in the component, because this is the part that has
 * been got wrong twice. It is invisible in every functional test (the rows are right, the JSON
 * is right; only the columns are absurd) and invisible in a screenshot taken at the width the
 * developer happens to use. `widths.test.ts` pins it at the widths nobody develops at.
 *
 * The rules, in the order they matter — lifted here unchanged from the component (#346, and
 * f984058b, which corrected the bargain below after driving /companies at 1100px):
 *
 * 1. **A declared width is a claim, not a possession.** `table-fixed` honours a width exactly,
 *    which is what makes `truncate` work — and it also means somebody has to pay when the
 *    columns no longer fit.
 * 2. **The identity column is not the one who pays.** The flexible column is *reserved*
 *    `FLEX_TARGET` before any fixed column keeps a pixel it asked for; the fixed ones then give
 *    way proportionally, each to `FIXED_MIN`. Reserving only the floor was the wrong bargain —
 *    the record's name fell the whole way to 160 while a column of em-dashes gave up four.
 * 3. **A floor is what a column survives on**, not what it is owed. Below those the grid stops
 *    shrinking and scrolls sideways, which is the honest answer for a set of columns genuinely
 *    too wide for the screen.
 * 4. **A width the user dragged is an instruction, not a default** — never shrunk, never floored.
 * 5. **Round down.** A dozen columns each rounded up sum past the box, and the grid answers a
 *    two-pixel overshoot with a scrollbar.
 * 6. **What the grid asks the *page* for is a separate number** (`natural`): every column at the
 *    width it wants, which is what lets a list outgrow the shell's reading measure instead of
 *    truncating inside it — see `core/ui/measure.svelte.ts`.
 */

/** The floor under the flexible column: the record's own name stays readable or nothing does. */
export const FLEX_MIN = 160;

/** The floor under a fixed column — wide enough for a short chip, a date or an amount. */
export const FIXED_MIN = 90;

/** What the flexible column is reserved before any fixed column keeps a pixel it asked for. */
export const FLEX_TARGET = 280;

/** The scroll box's own 1px rule, twice: a claim that omits it grants a page two pixels short. */
const BOX_BORDER = 2;

/** What this module needs of a column; `ColumnSpec` satisfies it. */
export interface WidthColumn {
  key: string;
  width?: number;
  flex?: boolean;
  primary?: boolean;
}

export interface TableLayout {
  /** The column that absorbs the slack, and therefore has no width of its own. */
  flexKey?: string;
  /** What every declared width is multiplied by; 1 when everything fits. */
  shrink: number;
  /** The flexible column's *floor* — what it keeps when the reservation could not be bought. */
  flexFloor: number;
  /** The width this grid would like the page to be — see `measure.svelte.ts`. */
  natural: number;
  /** `min-width` for the table, the one value a fixed layout reads to protect the flex column. */
  minWidth: number;
  /** Per column: the px width to declare, or `undefined` for "auto" (the flexible one). */
  widths: Record<string, number | undefined>;
}

/**
 * The column that absorbs whatever the fixed ones leave. A column that says `flex` wins;
 * otherwise the primary one, which is the long column on most lists; otherwise the first.
 * Always exactly one, so the declared widths always sum to less than the table.
 */
export function flexColumnKey(columns: WidthColumn[]): string | undefined {
  return (columns.find((c) => c.flex) ?? columns.find((c) => c.primary) ?? columns[0])?.key;
}

/**
 * @param columns  every visible column, in order
 * @param dragged  widths the user dragged, by key — authoritative
 * @param viewport the scroll box's own width in px; **0 means unmeasured**, i.e. do not shrink,
 *                 which is what keeps the SSR HTML identical to what it always was
 * @param chrome   the gutters no column declares: the checkbox cell and the ⋯ cell
 */
export function tableLayout(
  columns: WidthColumn[],
  dragged: Record<string, number>,
  viewport: number,
  chrome: number,
): TableLayout {
  const flexKey = flexColumnKey(columns);
  /** What the flexible column asks the *page* for; the layout reserves `FLEX_TARGET` instead. */
  const flexClaim = columns.find((c) => c.key === flexKey)?.width ?? FLEX_TARGET;

  let shrinkable = 0;
  let pinned = 0;
  for (const column of columns) {
    if (column.key === flexKey) continue; // reserved below, never shrunk with the others
    const drag = dragged[column.key];
    if (drag !== undefined) pinned += drag;
    else shrinkable += column.width ?? 0;
  }

  const reserved = dragged[flexKey ?? ""] ?? FLEX_TARGET;
  const room = viewport - chrome - pinned - reserved;
  const shrink =
    !viewport || shrinkable === 0 || room >= shrinkable ? 1 : Math.max(0, room) / shrinkable;

  const widths: Record<string, number | undefined> = {};
  for (const column of columns) {
    const drag = dragged[column.key];
    if (drag !== undefined) widths[column.key] = drag;
    else if (column.key === flexKey)
      widths[column.key] = undefined; // absorbs the slack
    else if (column.width === undefined) widths[column.key] = undefined;
    else
      widths[column.key] =
        shrink === 1 ? column.width : Math.max(FIXED_MIN, Math.floor(column.width * shrink));
  }

  /**
   * The floor, not the target: what the flexible column keeps when even shrinking every fixed
   * column to its own minimum could not buy the reservation. Expressed as the table's
   * `min-width`, the one value a fixed layout reads that can protect a column with no width.
   */
  const flexFloor = dragged[flexKey ?? ""] ?? FLEX_MIN;

  const minWidth =
    columns.reduce((sum, c) => sum + (widths[c.key] ?? (c.key === flexKey ? flexFloor : 0)), 0) +
    chrome;

  const natural =
    columns.reduce(
      (sum, c) => sum + (dragged[c.key] ?? (c.key === flexKey ? flexClaim : (c.width ?? 0))),
      0,
    ) +
    chrome +
    BOX_BORDER;

  return { flexKey, shrink, flexFloor, natural, minWidth, widths };
}
