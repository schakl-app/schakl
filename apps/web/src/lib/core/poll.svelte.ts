import { untrack } from "svelte";

/**
 * Re-run a load while a screen is waiting on work it did not do itself.
 *
 * An SSR load is a photograph. That is right for almost everything here, and wrong for the one
 * shape where the row's next state is produced by a worker rather than by the user: a report
 * mid-generation, an import mid-run. There the screen says "bezig…" and then says it for ever,
 * because nothing ever asks again — which is indistinguishable, to the person looking at it,
 * from the job having hung.
 *
 * Two rules are worth stating because getting either wrong is the bug this replaces:
 *
 * * **Poll a condition, not a timer.** The interval exists only while `active()` is true and is
 *   torn down the moment it is not, so a finished report costs nothing and a screen left open
 *   overnight is not still talking to the API in the morning.
 * * **Invalidate a dependency, not everything.** Callers pass `invalidate("some:key")` against a
 *   `depends()` their own load declared; `invalidateAll()` would re-run the layout loads too and
 *   turn one cheap re-read into the whole page's worth of API calls every few seconds.
 *
 * Call during component initialisation, like any `$effect`.
 */
export function pollWhile(active: () => boolean, refresh: () => unknown, everyMs = 4000): void {
  $effect(() => {
    if (!active()) return;
    // `untrack`: the refresh writes the very state `active()` reads, and a tracked write inside
    // the effect that owns the read restarts the interval on every tick.
    const timer = setInterval(() => untrack(() => void refresh()), everyMs);
    return () => clearInterval(timer);
  });
}
