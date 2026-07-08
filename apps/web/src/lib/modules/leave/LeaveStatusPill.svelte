<script lang="ts">
  /** The one status pill for a leave request — identical wherever a request appears. */
  import { t } from "$lib/core/i18n";

  let { status }: { status: string } = $props();

  const CLASSES: Record<string, string> = {
    pending: "bg-amber-100 text-amber-800",
    approved: "bg-green-100 text-green-700",
    rejected: "bg-red-100 text-red-700",
    cancelled: "bg-neutral-100 text-neutral-500",
  };
</script>

<span
  class="inline-flex rounded-full px-2 py-0.5 text-xs font-medium {CLASSES[status] ??
    'bg-neutral-100 text-neutral-600'}"
>
  {t(`leave.status.${status}`)}
</span>
