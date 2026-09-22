<script lang="ts">
  /** A meeting's state as one pill. Colour follows meaning; the label always says it in words. */
  import StateMark from "$lib/core/ui/StateMark.svelte";

  import { statusLabel, statusState } from "./format";

  let { status }: { status: string } = $props();
  const state = $derived(statusState(status));
</script>

{#if state === "neutral"}
  <span
    class="inline-flex items-center rounded-full bg-surface px-2.5 py-1 text-xs text-text-muted"
  >
    {statusLabel(status)}
  </span>
{:else}
  <StateMark {state} label={statusLabel(status)} variant="chip" />
{/if}
