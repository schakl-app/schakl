<script lang="ts">
  /**
   * One budget-burn block, on the one documented scale (`core/burn.ts`, docs/UX.md).
   *
   * It exists because there were four of these and one had drifted: the task card hand-rolled
   * the 75/100 ladder in `bg-green-500`/`bg-amber-500`/`bg-red-500` and clamped its bar without
   * ever reading `burn.ts`, so a task 40 % over budget was drawn by different code than a
   * project 40 % over budget. Everything a burn surface can get wrong — the thresholds, the
   * unclamped remainder, the clamped width — is decided here now, once.
   *
   * **Unit-agnostic on purpose.** A task budgets minutes, a project budgets hours, and the
   * sentence for "what is left" differs per module. So the caller passes the two raw numbers
   * (which decide the colour) *and* the already-formatted strings (which say it in the
   * caller's unit and vocabulary). That is what lets `time`, `tasks` and a table cell share
   * one component without core learning any of their words.
   *
   * `variant="block"` is the card shape: label, remainder, bar, spend. `variant="inline"` is
   * the table-cell shape: the figure with a hairline bar under it, the remainder on hover.
   */
  import { burnBarClass, burnBarWidth, burnPct, burnTextClass } from "$lib/core/burn";

  let {
    spent,
    budget,
    spentText,
    remainingText,
    titleText,
    label,
    noteText,
    variant = "block",
  }: {
    /** Consumed, in whatever unit the caller formats. Drives the colour, never the text. */
    spent: number;
    /** The allowance. `null` ⇒ nothing to burn, so no bar is drawn. */
    budget: number | null | undefined;
    /** "1u 30m / 3u" — what was spent of what. */
    spentText: string;
    /** "1u 30m over" / "20m over budget". Omitted when there is no allowance to remain of. */
    remainingText?: string;
    /**
     * Hover text for the inline variant. Defaults to `remainingText`, which is what a cell with
     * nothing else to say wants; a caller with more (the period the figure counts over, hours the
     * budget never covered) composes the whole sentence and passes it here.
     */
    titleText?: string;
    /** Left of the remainder in the block variant. */
    label?: string;
    /** Trailing note in the block variant's footer (e.g. which agreement the budget came from). */
    noteText?: string;
    variant?: "block" | "inline";
  } = $props();

  const pct = $derived(burnPct(spent, budget));
</script>

{#if variant === "inline"}
  <span class="inline-flex flex-col items-end gap-1" title={titleText ?? remainingText}>
    <span class="whitespace-nowrap text-xs tabular-nums {burnTextClass(pct)}">{spentText}</span>
    {#if pct != null}
      <span class="h-1 w-full min-w-12 overflow-hidden rounded-full bg-surface">
        <!-- Clamp the bar, never the number (docs/UX.md). -->
        <span
          class="block h-full rounded-full {burnBarClass(pct)}"
          style="width: {burnBarWidth(pct)}%"
        ></span>
      </span>
    {/if}
  </span>
{:else}
  <div>
    {#if label || remainingText}
      <div class="flex items-baseline justify-between gap-2">
        {#if label}
          <span class="text-xs font-medium text-text-muted">{label}</span>
        {/if}
        {#if remainingText}
          <!-- Loud only when it is gone (UX Principle 4): `burnTextClass` shouts for "over"
               and stays quiet otherwise, so the colour is a state and not decoration. -->
          <span class="text-sm font-semibold tabular-nums {burnTextClass(pct)}"
            >{remainingText}</span
          >
        {/if}
      </div>
    {/if}
    {#if pct != null}
      <div class="mt-1.5 h-2 overflow-hidden rounded-full bg-surface">
        <div
          class="h-full rounded-full {burnBarClass(pct)}"
          style="width: {burnBarWidth(pct)}%"
        ></div>
      </div>
    {/if}
    <div class="mt-1.5 flex flex-wrap items-baseline justify-between gap-x-2 gap-y-0.5">
      <span class="text-xs tabular-nums text-text-muted">{spentText}</span>
      {#if noteText}
        <span class="min-w-0 truncate text-xs text-text-muted">{noteText}</span>
      {/if}
    </div>
  </div>
{/if}
