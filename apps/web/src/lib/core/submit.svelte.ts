import type { SubmitFunction } from "@sveltejs/kit";

/**
 * Tracks which submission of a surface is in flight (#242, #279), so the button that
 * fired it can spin (`Button`'s `loading`) and its siblings can disable. One instance per
 * component; key by action name or row id when a surface owns more than one form.
 *
 *   const busy = new InFlight();
 *   <form use:enhance={busy.wrap()}>            → <Button loading={busy.active}>
 *   <form use:enhance={busy.wrap(row.id)}>      → <Button loading={busy.is(row.id)} disabled={busy.active}>
 *   <form use:enhance={busy.wrap("save", fn)}>  → keeps `fn`'s callback semantics intact
 */
export class InFlight {
  #key = $state<string | null>(null);

  /** Something on this surface is in flight — disable sibling submits. */
  get active(): boolean {
    return this.#key !== null;
  }

  /** This submission is in flight — the button that fired it spins. */
  is(key = ""): boolean {
    return this.#key === key;
  }

  /**
   * Wrap a form's `use:enhance`: flags `key` in flight for the request's duration, then
   * defers to `fn`'s returned callback — or plain `update()` when there is none, which is
   * what bare `use:enhance` does. A `fn` that `cancel()`s never flags anything. A form
   * with two submit buttons passes a resolver instead of a string and keys off
   * `input.submitter` (the CSV import's preview/commit shape).
   */
  wrap(
    key: string | ((input: Parameters<SubmitFunction>[0]) => string) = "",
    fn?: SubmitFunction,
  ): SubmitFunction {
    return (input) => {
      let cancelled = false;
      const inner = fn?.({
        ...input,
        cancel: () => {
          cancelled = true;
          input.cancel();
        },
      });
      if (cancelled) return;
      this.#key = typeof key === "function" ? key(input) : key;
      return async (event) => {
        this.#key = null;
        const callback = await inner;
        if (callback) await callback(event);
        else await event.update();
      };
    };
  }
}
