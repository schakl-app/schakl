<script lang="ts">
  /**
   * One report, drawn from the column list for its view.
   *
   * Three rendering rules carry the API's integrity contract onto the screen, and each of them
   * is a way a table like this usually starts lying:
   *
   * - **A `null` is a dash, never a zero.** `cost_per_conversion: null` means the campaign had
   *   no conversions, so a cost per conversion does not exist. `€ 0,00` claims it was free.
   * - **A ratio is a fraction and is multiplied here, once.** `0.0453` → `4,5 %`.
   * - **Money uses the *account's* currency, not the tenant's.** `fmtMoney` reads the org's
   *   currency, which is the right default everywhere else in the product and wrong here: an
   *   agency in Amsterdam runs accounts billed in GBP and SEK, and labelling those `€` would
   *   misstate every figure on the page.
   */
  import { dateLocale, fmtDateTime, fmtNumber } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import type { ReportColumn } from "./columns";
  import type { GoogleAdsMetrics } from "./types";

  let {
    columns,
    rows,
    totals = null,
    currency = null,
  }: {
    columns: ReportColumn[];
    rows: Record<string, unknown>[];
    totals?: GoogleAdsMetrics | null;
    currency?: string | null;
  } = $props();

  const money = $derived((value: number) =>
    new Intl.NumberFormat(
      dateLocale(),
      // The **account's** own currency. Falling back to a plain number rather than guessing a
      // symbol: an unlabelled figure is honest, a wrong symbol is not.
      currency
        ? { style: "currency", currency, maximumFractionDigits: 2 }
        : { style: "decimal", maximumFractionDigits: 2 },
    ).format(value),
  );

  /** Google's enum names translated where we have a word for them, echoed where we do not. */
  function enumLabel(value: string): string {
    const key = `google_ads.enum.${value.toLowerCase()}`;
    const translated = t(key);
    return translated === key ? value.replaceAll("_", " ").toLowerCase() : translated;
  }

  function render(column: ReportColumn, value: unknown): string {
    // The dash *is* the rendering of "not computable". Reached before any coercion, because
    // `Number(null)` is 0 and that is exactly the lie this guard exists to prevent.
    if (value === null || value === undefined || value === "") return "–";
    switch (column.kind) {
      case "money":
        return money(Number(value));
      case "ratio":
        return `${fmtNumber(Number(value) * 100, 1)} %`;
      case "number":
        return fmtNumber(Number(value));
      case "enum":
        return enumLabel(String(value));
      case "datetime":
        return fmtDateTime(String(value).replace(" ", "T"));
      default:
        return String(value);
    }
  }

  interface FieldChange {
    field: string;
    from: string | null;
    to: string | null;
  }
</script>

{#if rows.length === 0}
  <p class="text-sm text-text-muted">{t("google_ads.table.empty")}</p>
{:else}
  <!-- Wide reports scroll inside their own container; the page body never scrolls sideways. -->
  <div class="overflow-x-auto rounded-xl border border-border bg-surface-raised">
    <table class="w-full min-w-max text-sm">
      <thead>
        <tr class="border-b border-border text-left">
          {#each columns as column (column.key)}
            <th
              class="px-3 py-2 text-xs font-medium text-text-muted {column.numeric
                ? 'text-right'
                : ''}"
            >
              {column.label()}
            </th>
          {/each}
        </tr>
      </thead>
      <tbody class="divide-y divide-border">
        {#each rows as row, index (index)}
          <tr>
            {#each columns as column (column.key)}
              <td class="px-3 py-2 {column.numeric ? 'text-right tabular-nums' : ''}">
                {#if column.kind === "changes"}
                  <!-- The whole point of the change read: what the value was and what it became. -->
                  <ul class="space-y-0.5">
                    {#each (row[column.key] ?? []) as FieldChange[] as change (change.field)}
                      <li class="text-xs">
                        <span class="text-text-muted">{change.field}</span>
                        <span class="text-text">
                          {change.from ?? "–"} → {change.to ?? "–"}
                        </span>
                      </li>
                    {/each}
                  </ul>
                {:else}
                  {render(column, row[column.key])}
                {/if}
              </td>
            {/each}
          </tr>
        {/each}
      </tbody>
      {#if totals}
        <tfoot>
          <tr class="border-t border-border font-medium">
            {#each columns as column, index (column.key)}
              <td class="px-3 py-2 {column.numeric ? 'text-right tabular-nums' : ''}">
                {#if index === 0}
                  {t("google_ads.table.total")}
                {:else if column.numeric && column.key in totals}
                  <!-- Ratios here were re-derived from the summed components by the API, not
                       averaged across the rows above (the average of two CTRs is not the CTR
                       of two rows). -->
                  {render(column, (totals as unknown as Record<string, unknown>)[column.key])}
                {/if}
              </td>
            {/each}
          </tr>
        </tfoot>
      {/if}
    </table>
  </div>
{/if}
