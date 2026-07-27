<script lang="ts">
  /**
   * The mapping step: one row per column **of the user's file**, each pointing at a target
   * column of the entity (or at nothing).
   *
   * Rows are the file's columns rather than the entity's on purpose. The user is looking at
   * their own spreadsheet, and the question they can answer is "what is this column of mine?"
   * — not "which of my columns is the klantnummer?", which makes them search a list they
   * didn't write. It also means a file with columns this system knows nothing about reads as
   * ordinary ("niet importeren"), which is the case the whole feature exists for.
   *
   * Every row shows real sample cells from the file. That is what makes a wrong encoding, a
   * shifted column or a header on the wrong row obvious *before* anything is written, and it
   * is why the samples are always visible rather than behind a toggle.
   */
  import { t } from "$lib/core/i18n";
  import Combobox from "$lib/core/ui/Combobox.svelte";

  import type { ImpexColumn, InspectReport } from "./actions.server";

  let {
    inspect,
    columns,
    locale,
    mapping = $bindable({}),
    matchKey = $bindable(""),
  }: {
    inspect: InspectReport;
    columns: ImpexColumn[];
    /** Active UI locale — custom-field labels are tenant data, resolved client-side (§13). */
    locale: string;
    /** file column index → target key ("" = don't import). */
    mapping?: Record<number, string>;
    matchKey?: string;
  } = $props();

  function label(column: ImpexColumn): string {
    // A tenant's own custom field carries tenant labels (§13 data, resolved here — the API
    // never picks a locale for tenant content); everything else has an i18n key. `t` returns
    // the key itself when a message is missing, so an unlabelled column is still mappable.
    const labels = (column.label_i18n ?? {}) as Record<string, string>;
    if (column.label_i18n) {
      return labels[locale] || labels.nl || labels.en || column.key;
    }
    return column.label_key ? t(column.label_key) : column.key;
  }

  const groups = $derived.by(() => {
    const byId = new Map<string, { title: string; items: ImpexColumn[] }>();
    for (const column of columns) {
      if (column.readonly) continue; // export-only: nothing to map into
      const id = column.source === "extension" ? `module:${column.module}` : column.source;
      const title =
        column.source === "extension"
          ? t(`impex.group.${column.module}`)
          : t(`impex.group.${column.source}`);
      if (!byId.has(id)) byId.set(id, { title, items: [] });
      byId.get(id)!.items.push(column);
    }
    return [...byId.values()];
  });

  // One flat option list with the group as the hint — the house Combobox searches hints too,
  // so typing "contact" finds the contributed columns and typing a Dutch label finds the
  // custom fields, without a second grouped-select component nobody else would reuse.
  const options = $derived(
    groups.flatMap((group) =>
      group.items.map((column) => ({
        value: column.key,
        label: label(column) + (column.required ? " *" : ""),
        hint: `${group.title} · ${column.key}`,
      })),
    ),
  );

  const taken = $derived(new Set(Object.values(mapping).filter(Boolean)));
  const matchOptions = $derived(
    columns
      .filter((column) => column.natural_key && taken.has(column.key))
      .map((column) => ({ value: column.key, label: label(column), hint: column.key })),
  );
</script>

<div class="overflow-x-auto">
  <table class="w-full min-w-[34rem] text-sm">
    <thead>
      <tr class="border-b border-border text-left text-xs uppercase text-text-muted">
        <th class="py-2 pr-3 font-medium">{t("impex.mapping.source_column")}</th>
        <th class="py-2 pr-3 font-medium">{t("impex.mapping.samples")}</th>
        <th class="py-2 font-medium">{t("impex.mapping.target_column")}</th>
      </tr>
    </thead>
    <tbody class="divide-y divide-border">
      {#each inspect.columns as column (column.index)}
        <tr>
          <td class="py-2 pr-3 align-top">
            <span class="font-medium text-text">
              {column.header || t("impex.mapping.column_n", { n: column.index + 1 })}
            </span>
          </td>
          <td class="py-2 pr-3 align-top text-xs text-text-muted">
            {#if (column.samples ?? []).length}
              <ul class="space-y-0.5">
                {#each column.samples ?? [] as sample, index (index)}
                  <li class="truncate">{sample}</li>
                {/each}
              </ul>
            {:else}
              <span class="italic">{t("impex.mapping.no_samples")}</span>
            {/if}
          </td>
          <td class="py-2 align-top">
            <Combobox
              items={options}
              name={`map_${column.index}`}
              bind:value={mapping[column.index]}
              placeholder={t("impex.mapping.skip")}
              ariaLabel={t("impex.mapping.target_for", {
                column: column.header || String(column.index + 1),
              })}
              listClass="w-72"
            />
          </td>
        </tr>
      {/each}
    </tbody>
  </table>
</div>

{#if matchOptions.length > 1}
  <div class="mt-4 max-w-sm">
    <!--
      Only offered when the file carries more than one key it *could* match on. With one (or
      none) there is no decision to make, and a picker with a single option is a question the
      user has to read before discovering it wasn't one.
    -->
    <label for="impex-match-key" class="mb-1 block text-sm font-medium text-text">
      {t("impex.mapping.match_on")}
    </label>
    <Combobox
      items={matchOptions}
      name="match_key"
      id="impex-match-key"
      bind:value={matchKey}
      placeholder={t("impex.mapping.match_auto")}
    />
    <p class="mt-1 text-xs text-text-muted">{t("impex.mapping.match_hint")}</p>
  </div>
{/if}
