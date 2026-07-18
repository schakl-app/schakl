<script lang="ts">
  /**
   * The document line editor (issue #207): a repeater with a tax picker per line and a
   * live totals preview. The preview mirrors the API's math for display; the server
   * recomputes on save and is the authority (#48). Lines post as one JSON hidden field
   * (`lines`) — an edit surface has exactly one save button (docs/UX.md).
   */
  import { Trash2 } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import { computePreview, type EditableLine } from "./calc";
  import { docMoney, taxRateLabel, type TaxRate } from "./types";

  interface ProductPreset {
    id: string;
    name: string;
    description?: string | null;
    unit?: string | null;
    unit_price: string | number;
    tax_rate_id?: string | null;
  }

  let {
    lines = $bindable([] as EditableLine[]),
    taxRates,
    products = [],
    defaultTaxRateId = "",
    currency,
    locale,
    pricesIncludeTax = false,
    formId,
  }: {
    lines: EditableLine[];
    taxRates: TaxRate[];
    /** The tenant's default products (owner request); empty hides the picker. */
    products?: ProductPreset[];
    defaultTaxRateId?: string;
    currency: string;
    locale: string;
    pricesIncludeTax?: boolean;
    formId?: string;
  } = $props();

  const activeRates = $derived(taxRates.filter((r) => r.active));
  const money = (value: number) => docMoney(value, currency, locale);
  const preview = $derived(
    computePreview(lines, taxRates, pricesIncludeTax, (rate) => taxRateLabel(rate, locale)),
  );
  const linesJson = $derived(
    JSON.stringify(
      lines
        .filter((line) => line.description.trim())
        .map((line) => ({
          description: line.description.trim(),
          quantity: line.quantity || "1",
          unit: line.unit || null,
          unit_price: line.unit_price || "0",
          tax_rate_id: line.tax_rate_id || null,
          ...(line.time_entry_id ? { time_entry_id: line.time_entry_id } : {}),
        })),
    ),
  );

  function addLine() {
    lines = [
      ...lines,
      { description: "", quantity: "1", unit: "", unit_price: "", tax_rate_id: defaultTaxRateId },
    ];
  }
  /** Drop a default product onto the document (owner request): the pick *copies* the
   *  preset onto a line — the line stays free text and snapshots what it copied. */
  function addProduct(productId: string) {
    const product = products.find((p) => p.id === productId);
    if (!product) return;
    const line = {
      description: product.description || product.name,
      quantity: "1",
      unit: product.unit ?? "",
      unit_price: String(Number(product.unit_price)),
      tax_rate_id: product.tax_rate_id || defaultTaxRateId,
    };
    // Replace a still-empty trailing line instead of stacking under it.
    const last = lines[lines.length - 1];
    if (last && !last.description.trim() && !last.unit_price) {
      lines = [...lines.slice(0, -1), line];
    } else {
      lines = [...lines, line];
    }
    productPick = "";
  }
  let productPick = $state("");
  function removeLine(index: number) {
    lines = lines.filter((_, i) => i !== index);
  }

  const cellClass =
    "w-full rounded-lg border border-border bg-surface-raised px-2 py-1.5 text-sm outline-none focus:border-brand";
</script>

<div class="space-y-2">
  <!-- Header row (desktop); on mobile each line is its own labelled card row. -->
  <div
    class="hidden gap-2 text-xs font-semibold uppercase tracking-wide text-text-muted sm:grid sm:grid-cols-[1fr_5rem_5rem_7rem_10rem_7rem_2rem]"
  >
    <span>{t("invoicing.line.description")}</span>
    <span class="text-right">{t("invoicing.line.quantity")}</span>
    <span>{t("invoicing.line.unit")}</span>
    <span class="text-right">{t("invoicing.line.unit_price")}</span>
    <span>{t("invoicing.line.tax")}</span>
    <span class="text-right">{t("invoicing.line.amount")}</span>
    <span></span>
  </div>
  {#each lines as line, index (index)}
    <div
      class="grid grid-cols-2 items-center gap-2 rounded-lg border border-border p-2 sm:grid-cols-[1fr_5rem_5rem_7rem_10rem_7rem_2rem] sm:border-0 sm:p-0"
    >
      <input
        class="{cellClass} col-span-2 sm:col-span-1"
        placeholder={t("invoicing.line.description")}
        aria-label={t("invoicing.line.description")}
        bind:value={line.description}
      />
      <input
        class="{cellClass} text-right"
        type="number"
        step="0.01"
        aria-label={t("invoicing.line.quantity")}
        bind:value={line.quantity}
      />
      <input
        class={cellClass}
        placeholder={t("invoicing.line.unit")}
        aria-label={t("invoicing.line.unit")}
        bind:value={line.unit}
      />
      <input
        class="{cellClass} text-right"
        type="number"
        step="0.01"
        aria-label={t("invoicing.line.unit_price")}
        bind:value={line.unit_price}
      />
      <select class={cellClass} aria-label={t("invoicing.line.tax")} bind:value={line.tax_rate_id}>
        <option value="">{t("invoicing.line.no_tax")}</option>
        {#each activeRates as rate (rate.id)}
          <option value={rate.id}>{taxRateLabel(rate, locale)}</option>
        {/each}
      </select>
      <span class="text-right text-sm tabular-nums text-text">
        {money(Number(line.quantity || 0) * Number(line.unit_price || 0))}
      </span>
      <button
        type="button"
        class="justify-self-end text-text-muted hover:text-red-600 dark:hover:text-red-400"
        aria-label={t("invoicing.line.remove")}
        onclick={() => removeLine(index)}
      >
        <Trash2 size={15} />
      </button>
    </div>
  {/each}
  <div class="flex flex-wrap items-center gap-3">
    <button type="button" class="text-sm font-medium text-brand hover:underline" onclick={addLine}>
      ＋ {t("invoicing.line.add")}
    </button>
    {#if products.length > 0}
      <div class="w-56 min-w-0">
        <Combobox
          items={products.map((p) => ({
            value: p.id,
            label: p.name,
            hint: docMoney(Number(p.unit_price), currency, locale),
          }))}
          name="_product_pick"
          bind:value={productPick}
          id="line-product-pick"
          placeholder={t("invoicing.line.add_product")}
          onselect={addProduct}
        />
      </div>
    {/if}
  </div>

  <input type="hidden" name="lines" value={linesJson} form={formId} />

  <dl class="ml-auto w-64 space-y-1 border-t border-border pt-2 text-sm">
    <div class="flex justify-between">
      <dt class="text-text-muted">{t("invoicing.field.subtotal")}</dt>
      <dd class="tabular-nums text-text">{money(preview.subtotal)}</dd>
    </div>
    {#each preview.groups as group (group.ratePct + group.category)}
      <div class="flex justify-between">
        <dt class="text-text-muted">{group.name}</dt>
        <dd class="tabular-nums text-text">{money(group.tax)}</dd>
      </div>
    {/each}
    <div class="flex justify-between border-t border-border pt-1 font-semibold">
      <dt class="text-text">{t("invoicing.field.total")}</dt>
      <dd class="tabular-nums text-text">{money(preview.total)}</dd>
    </div>
  </dl>
</div>
