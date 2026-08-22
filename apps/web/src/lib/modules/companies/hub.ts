/**
 * How the client hub fits its cards together (#403).
 *
 * The registry hands the company page an ordered list of panels, each declaring how wide it
 * wants to be (`size`) and whether this client has anything in it yet (`empty`, #364). #364's
 * answer to *arranging* them cut the list into runs of **consecutive** halves and gave each run
 * the layout its own count deserved — one half alone took the whole row, two matched, three or
 * more packed into a CSS multi-column stack — and it was drawn over a list the empty panels had
 * already been filtered out of. Each half of that is defensible; together they made a panel's
 * width and reading position a function of *which of its neighbours happen to be empty for this
 * client*. Measured on one instance at one viewport: Contactpersonen 488 px wide on two clients
 * and 992 px on a third, and Uren — a half sitting between two fulls, so always alone on its run
 * — drawn 992 px wide and 509 px tall on every client that has hours. That is the "de volgorde
 * lijkt per klant te verschillen" the team reported, and it was not an impression.
 *
 * Three rules replace it, and together they are what a *vaste indeling* means:
 *
 * 1. **A panel's declared `size` is what it gets.** A half is half wide whether or not it has a
 *    neighbour. The slot beside a lone half is filled by the next panel, or by nothing — never
 *    by stretching the card, which is the one thing that cannot be done while width depends on
 *    neighbours.
 * 2. **The arrangement is computed over the *full* ordered list, folded panels included**, and
 *    the folded ones are omitted afterwards. Every panel is dealt a seat, so a client with
 *    nothing in Projecten finds Contactpersonen in the lane a client who has everything finds it
 *    in. It is also why a folded *full-width* panel still ends the run of halves around it: a
 *    run boundary that moves is the whole bug.
 * 3. **Two lanes, assigned in declared order, free to end at different heights.** The multi-
 *    column stack this replaces is column-major — you read down the left lane and then down the
 *    right — which is the second reason two clients with the same panels looked reordered.
 *    Alternating seats read top-to-bottom in both lanes, on every client.
 *
 * The lanes are a desktop shape only; the page collapses them to one column in declared order
 * below `lg` (see the `lane` snippet's `display: contents` + flex `order`).
 */

/** The panel fields the arrangement reads — a structural subset of the API's `PanelData`. */
export type HubPanel = {
  key: string;
  size: string;
  empty: boolean;
};

/** A card and the seat it was dealt: the lane is `seat % 2`, the mobile order is `seat`. */
export type HubSeat<P extends HubPanel> = { panel: P; seat: number };

/** One row of the lane: a full-width card, or a run of halves split over two lanes. */
export type HubRow<P extends HubPanel> =
  { key: string; kind: "full"; panel: P } | { key: string; kind: "lanes"; lanes: HubSeat<P>[][] };

/**
 * Deal `panels` (in declared order, empties included) into rows, drawing only what is visible.
 *
 * `unfolded` holds the keys of empty panels the reader opened from the ＋ strip — an empty panel
 * is dealt its seat either way, so unfolding one never moves the cards around it.
 */
export function arrangePanels<P extends HubPanel>(
  panels: readonly P[],
  unfolded: ReadonlySet<string>,
): HubRow<P>[] {
  const rows: HubRow<P>[] = [];
  let lanes: HubSeat<P>[][] | null = null;
  // Seats *dealt*, folded panels included — dealing them is what keeps the assignment the same
  // from client to client.
  let seat = 0;
  for (const panel of panels) {
    const drawn = !panel.empty || unfolded.has(panel.key);
    if (panel.size !== "half") {
      lanes = null;
      if (drawn) rows.push({ key: panel.key, kind: "full", panel });
      continue;
    }
    if (!lanes) {
      lanes = [[], []];
      seat = 0;
      rows.push({ key: panel.key, kind: "lanes", lanes });
    }
    if (drawn) lanes[seat % 2].push({ panel, seat });
    seat += 1;
  }
  // A run whose every half folded into the ＋ strip draws nothing at all.
  return rows.filter((row) => row.kind === "full" || row.lanes.some((lane) => lane.length > 0));
}
