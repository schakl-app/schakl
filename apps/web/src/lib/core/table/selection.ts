/**
 * Range selection for the shared `DataTable` (#301).
 *
 * Ticking forty auto-matched emails one checkbox at a time is how a bulk action stops being one,
 * so shift-click extends the selection from the last row ticked on its own (the *anchor*) to the
 * row just clicked. This lives outside the component because the ordering rules below are the
 * whole of the feature and the component is the one place they cannot be tested.
 */

/**
 * The ids a shift-click leaves selected.
 *
 * `order` is the **visible** row order — the flattened sections minus the collapsed ones. A range
 * that quietly swept up rows nobody could see is exactly how a bulk reject reaches an email that
 * was never read. It is also deduplicated: `groupBy` may list one record under several sections,
 * and a checkbox knows the row's id, not which copy was clicked, so there is no second position
 * to measure to.
 *
 * The span takes the state the clicked row is moving to — tick to add it, untick to drop it,
 * which is the rule every mail client and file manager already put in the user's fingers. With no
 * usable anchor (nothing ticked yet, or either end scrolled out of the visible set) it degrades to
 * toggling the one row, never to selecting a range measured from somewhere the user cannot see.
 */
export function rangeSelection(
  order: string[],
  anchor: string | null,
  id: string,
  selected: string[],
): string[] {
  const has = (rid: string) => selected.includes(rid);
  const to = order.indexOf(id);
  const from = anchor === null ? -1 : order.indexOf(anchor);
  if (to < 0 || from < 0) return has(id) ? selected.filter((rid) => rid !== id) : [...selected, id];

  const range = order.slice(Math.min(from, to), Math.max(from, to) + 1);
  // Rows already in the target state are left where they are rather than re-added, so a span can
  // never land in the selection twice — the ids are posted to a bulk endpoint verbatim.
  return has(id)
    ? selected.filter((rid) => !range.includes(rid))
    : [...selected, ...range.filter((rid) => !has(rid))];
}
