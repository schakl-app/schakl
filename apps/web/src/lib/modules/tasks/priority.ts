/**
 * The priority rail (#395) — the left edge of a task row, in the priority's colour.
 *
 * The board printed the word **Normaal** twenty times in the same grey, so the one column that
 * was supposed to say what cannot slip said nothing at all. A pill on the `high` rows is right
 * and is not enough: it does not survive being one badge among six on a row that is one of
 * twenty. A rail is found by the eye before the text is read.
 *
 * Two rules hold it up.
 *
 * **A marker every row carries is not a marker.** Only the exceptional values draw one — `high`
 * in the palette's `late` red, `low` as a faint edge — and `normal`, which is most of any board,
 * draws a transparent rail so the rows still line up. The transparent one is the point: without
 * it, marking a row would shift its first column two pixels and the column would visibly wobble
 * down the page.
 *
 * **Never colour alone** (#404). The rail is a second reading of something already written: the
 * board's Prioriteit column prints the word, `TaskRow` prints `HOOG` beside the title, and the
 * task card says it in full. A reader who cannot separate the hues loses nothing.
 *
 * A finished task draws none of it. Its priority is history, and a red edge on a struck-through
 * title is the loudest possible way to say something that no longer matters.
 */

/** Tailwind classes for a row's left edge. Always a 2px rail, so nothing shifts. */
export function priorityRailClass(priority: string, done = false): string {
  if (done) return "border-l-2 border-l-transparent";
  if (priority === "high") return "border-l-2 border-l-red-500 dark:border-l-red-400";
  if (priority === "low") return "border-l-2 border-l-border";
  return "border-l-2 border-l-transparent";
}
