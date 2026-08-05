<script lang="ts">
  /** A report's state as one pill. Colour follows meaning; the label always says it in words. */
  import { statusClass, statusLabel } from "./format";

  let { status, size = "sm" }: { status: string; size?: "sm" | "xs" } = $props();
</script>

<span
  class="inline-flex items-center rounded-full font-medium {statusClass(status)} {size === 'xs'
    ? 'px-2 py-0.5 text-[11px]'
    : 'px-2.5 py-1 text-xs'}"
>
  {statusLabel(status)}
</span>
