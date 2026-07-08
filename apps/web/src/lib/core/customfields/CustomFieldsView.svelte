<script lang="ts">
  /**
   * Read-only rendering of custom values against their definitions (labels per locale).
   * Used by company panels and entity detail views. Values with no matching definition are
   * skipped (the definition may have been deactivated).
   */
  import type { CustomFieldDefinition } from "./types";
  import { fieldLabel } from "./types";

  let {
    definitions,
    values = {},
    locale,
  }: {
    definitions: CustomFieldDefinition[];
    values?: Record<string, unknown>;
    locale: string;
  } = $props();

  function display(value: unknown): string {
    if (value === null || value === undefined || value === "") return "—";
    if (Array.isArray(value)) return value.join(", ");
    if (typeof value === "boolean") return value ? "✓" : "—";
    return String(value);
  }

  const shown = $derived(definitions.filter((d) => values[d.key] !== undefined));
</script>

{#if shown.length > 0}
  <dl class="grid grid-cols-1 gap-3 sm:grid-cols-2">
    {#each shown as def (def.key)}
      <div>
        <dt class="text-xs font-medium uppercase tracking-wide text-neutral-500">
          {fieldLabel(def, locale)}
        </dt>
        <dd class="mt-1 text-sm text-neutral-900">{display(values[def.key])}</dd>
      </div>
    {/each}
  </dl>
{/if}
