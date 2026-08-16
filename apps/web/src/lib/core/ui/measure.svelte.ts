import { getContext, onDestroy, setContext } from "svelte";
import { SvelteMap } from "svelte/reactivity";

/**
 * The page measure, and how a grid asks for more of it.
 *
 * The (app) shell caps every screen at `--container-content` (1600px) because past a point
 * wider is not more readable, it is further to look. That is a rule about *reading*, and a
 * table is not read, it is looked things up in: its width is not a taste, it is the sum of the
 * columns the user switched on. On /tasks with every column shown the declared widths sum past
 * the cap, so the one column with no width of its own — the record's name, the only cell that
 * links out of the row — was handed its 160px floor and truncated nine rows of eleven, on a
 * 2560px monitor with 360px of unused screen on either side.
 *
 * So a grid may **claim** the width it actually needs, and the shell grants it twice bounded:
 * never below the measure (a narrow list still reads inside it, never stretched thin) and never
 * beyond the space that exists (`min(…, 100%)`). Tasks with twelve columns lands at 1812px —
 * wide enough that nothing truncates, and still 500px short of full bleed, which is the point.
 *
 * The claim is a number the columns already state, so it needs no measurement — but it is made
 * from an effect, so SSR renders the plain measure and the widening happens on hydration. A
 * screen that widens once is the cost of the shell not being able to see its own content.
 */
const KEY = Symbol("page-measure");

interface PageMeasure {
  claim(id: symbol, px: number): void;
  release(id: symbol): void;
}

/** Called once by the shell. Returns the widest live claim, or 0 for "just the measure". */
export function providePageMeasure(): () => number {
  const claims = new SvelteMap<symbol, number>();
  setContext<PageMeasure>(KEY, {
    claim: (id, px) => claims.set(id, px),
    release: (id) => claims.delete(id),
  });
  return () => Math.max(0, ...claims.values());
}

/**
 * The CSS the shell applies to the page and to the header's controls alike — they line up, so
 * they widen together or the avatar drifts away from the table it sits over.
 */
export function measureStyle(px: number): string | undefined {
  return px ? `max-width:min(max(var(--container-content), ${Math.round(px)}px), 100%)` : undefined;
}

/** Called by a page-level grid that knows how wide it wants to be. Released on unmount. */
export function claimPageMeasure(width: () => number): void {
  const shell = getContext<PageMeasure | undefined>(KEY);
  if (!shell) return; // rendered outside the (app) shell (a harness, a print view)
  const id = Symbol("grid");
  $effect(() => shell.claim(id, width()));
  onDestroy(() => shell.release(id));
}
