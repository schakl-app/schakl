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
 * weight it was drawn at. Width fills the container; the height is simply the design height.
 *
 * **The height is deliberately a constant, not a function of the width.** An earlier pass grew
 * it with the container to keep a very wide chart from flattening, and that is a scrollbar
 * oscillation waiting to happen: a taller chart makes the page taller, a vertical scrollbar
 * appears, the container narrows by ~15px, the chart shortens, the scrollbar goes away, and the
 * page flickers forever on whatever screen happens to sit at the knife-edge. It also stopped
 * being *needed* once the app shell capped its content measure (`--container-content` in
 * app.css), because no chart is handed 3000px any more. If a chart ever renders outside that
 * measure and reads as a strip, give it a taller design height — never one derived from its own
 * width.
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
 * Width of one bar in a grouped bar chart, given the width of its category slot.
 *
 * A fixed pixel width would be the naive companion to "stop scaling the type", and it is wrong
 * for exactly the reason the type rule is right: type has an absolute legible size and a bar
 * does not — a bar is read against its neighbours. Twelve 14px threads spaced 250px apart is a
 * worse chart than the one this replaced. So the bar is a proportion of its slot, floored at the
 * design width so narrow and mid-size containers are untouched, and never wider than the slot
 * can hold with `gap` between the pair.
 *
 * Unlike a height, this cannot oscillate: nothing about a bar's width changes the page's height.
 */
export function barWidth(slot: number, design: number, gap: number, share = 0.24): number {
  return Math.min(Math.max(design, slot * share), (slot - gap) / 2);
}
