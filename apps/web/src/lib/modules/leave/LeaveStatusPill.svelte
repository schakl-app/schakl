<script lang="ts">
  /** The one status pill for a leave request — identical wherever a request appears. */
  import { t } from "$lib/core/i18n";

  let { status }: { status: string } = $props();

  const CLASSES: Record<string, string> = {
    pending: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
    approved: "bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-300",
    rejected: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300",
    cancelled: "bg-surface text-text-muted",
  };
</script>

<span
  class="inline-flex rounded-full px-2 py-0.5 text-xs font-medium {CLASSES[status] ??
    'bg-surface text-text-muted'}"
>
  {t(`leave.status.${status}`)}
</span>
