<script lang="ts">
  /**
   * The one status pill for a time entry — identical in the Uren report and in the Uren panel on
   * a project (#42, #43). Unapproved hours are *marked*, never hidden: they burn the budget, so a
   * panel that quietly dropped them would report a smaller number than the bar above it.
   */
  import { t } from "$lib/core/i18n";

  import { entryStatus } from "./format";

  let {
    entry,
  }: {
    entry: { billable?: boolean; approved_at?: string | null; invoiced_at?: string | null };
  } = $props();

  const CLASSES: Record<string, string> = {
    open: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
    approved: "bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-300",
    to_invoice: "bg-brand/10 text-brand",
    invoiced: "bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-300",
  };

  const status = $derived(entryStatus(entry));
</script>

<span
  class="inline-flex whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium {CLASSES[
    status
  ]}"
>
  {t(`time.overview.status.${status}`)}
</span>
