<script lang="ts">
  /**
   * Hours against a budget in a table cell, with a burn bar (#25).
   *
   * `0 / 5 u` is **spent of budget** — the one meaning the bare form has anywhere in the app
   * (`core/hours.ts`, #340). This cell used to print the *remainder* in the same nine glyphs,
   * so the same project read `0 / 5 u` on My Day and `5 / 5 u` here, with the identical empty
   * bar under both. What is left is still the more useful sentence on a client list, so it is
   * still here — on hover, in words (`5 u over`), where it cannot be mistaken for the other
   * number. Over budget the remainder goes negative and red rather than clamping to a
   * reassuring zero; the bar's *width* clamps, because a bar cannot be 130 % long.
   *
   * With no budget there is nothing to remain, so this shows an em-dash — never a fabricated
   * total — while still reporting the hours that were spent. Hours the budget never covered
   * (unapproved, or on the client's unbudgeted work) are named in the tooltip and marked with a
   * `*`: excluded from the arithmetic, never dropped from the record.
   */
  import { hoursBurn, hoursSpentText, type HoursFields } from "$lib/core/hours";
  import BudgetBar from "$lib/core/ui/BudgetBar.svelte";

  let { hours }: { hours?: HoursFields | null } = $props();

  const burn = $derived(hoursBurn(hours));
  // Without a budget the whole record is what was logged, so a client whose hours all sit outside
  // a budgeted project still reports them rather than an unexplained zero.
  const loose = $derived(hoursSpentText(burn?.spent || (hours?.unbudgeted_hours ?? 0), null));
</script>

{#if !burn}
  <span class="text-text-muted">—</span>
{:else if burn.budget == null}
  <!-- No allowance to burn. The spend is still on the record. -->
  <span class="text-text-muted" title={burn.title}>
    —
    {#if burn.spent > 0 || hours?.unbudgeted_hours}
      <span class="ml-1 text-xs">({loose})</span>
    {/if}
  </span>
{:else}
  <!-- The one burn block (core/ui/BudgetBar.svelte): thresholds, the unclamped remainder and the
       clamped width are decided there, the words in core/hours.ts. -->
  <BudgetBar
    variant="inline"
    spent={burn.spent}
    budget={burn.budget}
    spentText={burn.caveats.length > 0 ? `${burn.spentText} *` : burn.spentText}
    remainingText={burn.remainingText}
    titleText={burn.title}
  />
{/if}
