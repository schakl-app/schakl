/**
 * The box a hand-rolled inline-SVG chart draws into.
 *
 * An SVG with a constant `viewBox` and `class="w-full"` has no size of its own — it has an
 * *aspect ratio*, and the browser scales every user unit inside it to whatever width it is
 * handed. A 720×200 design is therefore only 720×200 on a 720px-wide container: on a 3178px
 * screen it became 3130×869 with 59px axis labels, and on a 390px phone the same labels
 * rendered at 6px. One bug, both ends, and it never showed up on a laptop.
 *
 * The fix is to stop letting the viewBox imply a scale: measure the container and draw at
 * **1 user unit = 1 CSS px**, so 10px type is 10px type at every width and a stroke stays the
 * weight it was drawn at. Width still fills the container — the height is what stops being a
 * function of it, growing only gently and to a cap so a wide chart neither towers nor flattens
 * into an unreadable strip.
 *
 * The math lives here rather than in the components because it is the part worth pinning: a
 * chart that is the wrong size is perfectly valid SVG and passes every functional test.
 */

/**
 * Drawing width in user units = CSS px.
 *
 * `measured` is the container's `clientWidth`, which is 0 during SSR and until the first
 * client measurement — hence `fallback`, the width the chart was designed at, so the
 * server-rendered frame is sensible rather than degenerate. `min` keeps a chart that lands in a
 * collapsed or very narrow box from drawing a plot area of zero or negative width.
 */
export function chartWidth(measured: number, fallback: number, min: number): number {
  return Math.max(min, measured || fallback);
}

/**
 * Drawing height for a given width.
 *
 * `base` is the design height and the floor: a narrow container gets the full height rather
 * than a squashed one (the phone case above). Beyond `base * ratio` wide, the height grows with
 * the width so the plot keeps a readable aspect, and stops at `cap` so a very wide screen gets a
 * chart, not a wall.
 */
export function chartHeight(width: number, base: number, cap: number, ratio = 4): number {
  return Math.round(Math.min(cap, Math.max(base, width / ratio)));
}

/**
 * Width of one bar in a grouped bar chart, given the width of its category slot.
 *
 * A fixed pixel width would be the naive companion to "stop scaling the type", and it is wrong
 * for exactly the reason the type rule is right: type has an absolute legible size and a bar
 * does not — a bar is read against its neighbours. Twelve 14px threads spaced 250px apart is a
 * worse chart than the one this replaced. So the bar is a proportion of its slot, floored at the
 * design width so narrow and mid-size containers are untouched, and never wider than the slot
 * can hold with `gap` between the pair.
 */
export function barWidth(slot: number, design: number, gap: number, share = 0.24): number {
  return Math.min(Math.max(design, slot * share), (slot - gap) / 2);
}
