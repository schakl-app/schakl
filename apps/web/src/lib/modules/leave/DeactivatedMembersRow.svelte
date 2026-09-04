<script lang="ts">
  /**
   * The fold under a roster table: one row that says how many colleagues who no longer work
   * here it is hiding, and opens them.
   *
   * The leave module draws people in tables (the team balances, the entitlement table under
   * Instellingen → Verlof), where Instellingen → Gebruikers' dashed strip (#405) cannot sit
   * between two `<tr>`s — so this is that strip as a table row, spanning every column. The
   * host keeps the rows themselves: which columns a former colleague's row carries is the
   * table's business, and this component only decides whether they are drawn.
   *
   * Closed by default and absent when there is nobody to fold — the host renders it only for a
   * non-zero count, because a "Gedeactiveerd (0)" strip is a heading over a negative sentence.
   */
  import { ChevronDown } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";

  let {
    count,
    colspan,
    expanded = $bindable(false),
  }: {
    count: number;
    colspan: number;
    expanded?: boolean;
  } = $props();
</script>

<tr class="bg-surface/60">
  <td class="px-2 py-1.5" {colspan}>
    <button
      type="button"
      class="flex w-full items-center gap-2 rounded-lg border border-dashed border-border px-3 py-1.5 text-left text-sm font-medium text-text-muted hover:border-brand/50 hover:text-text"
      aria-expanded={expanded}
      onclick={() => (expanded = !expanded)}
    >
      <ChevronDown
        class="size-4 shrink-0 transition-transform {expanded ? '' : '-rotate-90'}"
        aria-hidden="true"
      />
      {t("leave.team.deactivated_section", { count })}
    </button>
  </td>
</tr>
