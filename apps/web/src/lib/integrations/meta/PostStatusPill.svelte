<script lang="ts">
  /** A post's state as one pill. Colour follows meaning; the label always says it in words. */
  import StateMark from "$lib/core/ui/StateMark.svelte";

  import { statusLabel, statusState } from "./format";

  let { status, label }: { status: string; label?: string } = $props();
  const state = $derived(statusState(status));
  const text = $derived(label ?? statusLabel(status));
</script>

{#if state === "neutral"}
  <span
    class="inline-flex items-center whitespace-nowrap rounded-full bg-surface px-2.5 py-1 text-xs text-text-muted"
  >
    {text}
  </span>
{:else}
  <StateMark {state} label={text} variant="chip" />
{/if}
