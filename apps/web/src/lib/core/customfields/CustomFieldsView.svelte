<script lang="ts">
  /**
   * Read-only rendering of custom values against their definitions (labels per locale).
   * Used by company panels and entity detail views. Values with no matching definition are
   * skipped (the definition may have been deactivated).
   *
   * Two corrections from #364, both of which reached the contact, project and website pages
   * through this one file. It was written in raw `text-neutral-500`/`-900`, which `app.css` says
   * in as many words not to do — in dark mode every value rendered near-black on near-black. And
   * a `date` field printed its stored ISO string (`2021-03-01`) at a Dutch reader, where every
   * other date in the app is European (§8).
   */
  import { fmtDateTime, fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import Markdown from "$lib/core/ui/Markdown.svelte";

  import type { CustomFieldDefinition } from "./types";
  import { fieldLabel, optionLabel } from "./types";

  let {
    definitions,
    values = {},
    locale,
  }: {
    definitions: CustomFieldDefinition[];
    values?: Record<string, unknown>;
    locale: string;
  } = $props();

  /** A stored option value is a key; the tenant's own label for it is what a reader wants. */
  function labelFor(def: CustomFieldDefinition, raw: unknown): string {
    const option = (def.options_json ?? []).find((o) => o.value === String(raw));
    return option ? optionLabel(option, locale) : String(raw);
  }

  function display(def: CustomFieldDefinition, value: unknown): string {
    if (value === null || value === undefined || value === "") return "—";
    if (Array.isArray(value)) {
      return value.length ? value.map((v) => labelFor(def, v)).join(", ") : "—";
    }
    if (typeof value === "boolean") return value ? t("common.yes") : t("common.no");
    if (def.data_type === "date" && typeof value === "string") return fmtNumericDate(value);
    if (def.data_type === "datetime" && typeof value === "string") return fmtDateTime(value);
    if (def.data_type === "select" || def.data_type === "multi_select") {
      return labelFor(def, value);
    }
    return String(value);
  }

  /** LONG_TEXT holds markdown (issue #66) — render it, not its source. */
  function isMarkdown(def: CustomFieldDefinition, value: unknown): boolean {
    return def.data_type === "long_text" && typeof value === "string" && value.trim() !== "";
  }

  const shown = $derived(definitions.filter((d) => values[d.key] !== undefined));
</script>

{#if shown.length > 0}
  <dl class="grid grid-cols-1 gap-3 sm:grid-cols-2">
    {#each shown as def (def.key)}
      <div>
        <dt class="text-xs font-medium uppercase tracking-wide text-text-muted">
          {fieldLabel(def, locale)}
        </dt>
        <dd class="mt-1 text-sm text-text">
          {#if isMarkdown(def, values[def.key])}
            <Markdown value={String(values[def.key])} />
          {:else}
            {display(def, values[def.key])}
          {/if}
        </dd>
      </div>
    {/each}
  </dl>
{/if}
