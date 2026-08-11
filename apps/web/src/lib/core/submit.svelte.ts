import type { SubmitFunction } from "@sveltejs/kit";

import { noticeFailedSubmit } from "./session-watch";

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
        // A refused submit is the moment unsaved work is most at risk, and the moment the app
        // explains itself worst: if the session died, every route reports it as whatever error
        // key it happens to use, and none of them can say "you are signed out". Ask — here,
        // because this wrapper is the one thing every enhanced form already goes through, so
        // no form has to remember. It is a question, not a conclusion (`noticeFailedSubmit`),
        // and SvelteKit resets a form only on success, so the typed values are still there when
        // the prompt appears over them.
        if (event.result.type === "failure" || event.result.type === "error") {
          void noticeFailedSubmit();
        }
        const callback = await inner;
        if (callback) await callback(event);
        else await event.update();
      };
    };
  }

  /**
   * Like {@link wrap}, for a form that **edits what already exists** — settings, a detail
   * view, anything reached by loading a record rather than starting a blank one.
   *
   * The difference is one flag: `update({ reset: false })`. A bare `use:enhance` resets the
   * form on success, and `HTMLFormElement.reset()` puts every control back to its
   * `defaultValue` — the `value` **attribute**, which a Svelte-managed input does not have.
   * Svelte then reads those reset values *back into* the bound state (it listens for `reset`
   * precisely so bindings stay truthful), so a `bind:value` field does not merely look blank:
   * the value is gone. Pressing Save wipes the field you just saved.
   *
   * Resetting only makes sense for a form you want emptied for the *next* entry (a create
   * form, a comment box). For an edit form there is nothing to reset to, so use this.
   * See docs/UX.md, "Saving must never blank the form".
   */
  keep(key: string | ((input: Parameters<SubmitFunction>[0]) => string) = ""): SubmitFunction {
    return this.wrap(key, () => async ({ update }) => {
      await update({ reset: false });
    });
  }

  /**
   * The deliberate opposite of {@link keep}: a form that **starts something new** — a create
   * form, a comment box, an invite — and should come back empty for the next entry.
   *
   * Behaviourally this is exactly `wrap()`, because emptying is what SvelteKit already does.
   * It exists so the choice is written down: `scripts/forms-check.mjs` fails a form that
   * carries typed-in controls and says neither `keep()` nor `clear()`, which is how the
   * "pressing Save blanked my text" bug kept coming back — nobody *decided* to reset, they
   * inherited it. See docs/UX.md, "Saving must never blank the form".
   */
  clear(key: string | ((input: Parameters<SubmitFunction>[0]) => string) = ""): SubmitFunction {
    return this.wrap(key, () => async ({ update }) => {
      await update({ reset: true });
    });
  }
}
