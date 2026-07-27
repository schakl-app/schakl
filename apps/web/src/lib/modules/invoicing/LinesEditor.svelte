<script lang="ts">
  /**
   * The document line editor (issue #207): a repeater with a tax picker per line and a
   * live totals preview. The preview mirrors the API's math for display; the server
   * recomputes on save and is the authority (#48). Lines post as one JSON hidden field
   * (`lines`) — an edit surface has exactly one save button (docs/UX.md).
   *
   * Three kinds of line, three ways to add one (owner request): hours, subscriptions and
   * ordinary product/service lines. The kind travels with the line to the document, which
   * groups and subtotals by it; a subscription pick also carries the **period** it bills,
   * so the cycle cron knows that month is already paid and never invoices it twice.
   */
  import { Trash2 } from "@lucide/svelte";

  import { fmtPeriod } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import { computePreview, type EditableLine } from "./calc";
  import { LINE_KINDS, docMoney, lineKindLabel, taxRateLabel } from "./types";
  import type { BillableSubscription, LineKind, TaxRate } from "./types";

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
    subscriptions = [],
    defaultTaxRateId = "",
    defaultHourlyRate = "",
    currency,
    locale,
    pricesIncludeTax = false,
    formId,
  }: {
    lines: EditableLine[];
    taxRates: TaxRate[];
    /** The tenant's default products; empty hides the picker. */
    products?: ProductPreset[];
    /** The client's active agreements; empty hides the subscription picker. */
    subscriptions?: BillableSubscription[];
    defaultTaxRateId?: string;
    /** The org's default hourly rate, so an "＋ uren" line starts priced. */
    defaultHourlyRate?: string | number;
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
          line_kind: line.line_kind ?? "product",
          quantity: line.quantity || "1",
          unit: line.unit || null,
          unit_price: line.unit_price || "0",
          tax_rate_id: line.tax_rate_id || null,
          ...(line.time_entry_id ? { time_entry_id: line.time_entry_id } : {}),
          // The period claim only travels as a pair; a half-claim is refused by the API.
          ...(line.subscription_id && line.period_end
            ? {
                subscription_id: line.subscription_id,
                period_start: line.period_start ?? null,
                period_end: line.period_end,
              }
            : {}),
        })),
    ),
  );

  function blank(kind: LineKind): EditableLine {
    return {
      description: "",
      line_kind: kind,
      quantity: "1",
      unit: kind === "hours" ? t("invoicing.from_time.hours_unit") : "",
      unit_price: kind === "hours" ? String(Number(defaultHourlyRate || 0) || "") : "",
      tax_rate_id: defaultTaxRateId,
    };
  }

  /** Replace a still-empty trailing line instead of stacking under it. */
  function append(...added: EditableLine[]) {
    const last = lines[lines.length - 1];
    const base = last && !last.description.trim() && !last.unit_price ? lines.slice(0, -1) : lines;
    lines = [...base, ...added];
  }

  function addLine(kind: LineKind = "product") {
    append(blank(kind));
  }

  /** Drop a default product onto the document: the pick *copies* the preset onto a line —
   *  the line stays free text and snapshots what it copied. */
  function addProduct(productId: string) {
    const product = products.find((p) => p.id === productId);
    if (!product) return;
    append({
      description: product.description || product.name,
      line_kind: "product",
      quantity: "1",
      unit: product.unit ?? "",
      unit_price: String(Number(product.unit_price)),
      tax_rate_id: product.tax_rate_id || defaultTaxRateId,
    });
    productPick = "";
  }

  /** Bill an agreement's next period by hand. Every line of the pick carries the same
   *  (subscription, period) claim, so the cycle cron finds the month taken. */
  function addSubscription(subscriptionId: string) {
    const sub = subscriptions.find((s) => s.id === subscriptionId);
    subscriptionPick = "";
    if (!sub) return;
    const period = periodLabel(sub);
    append(
      ...(sub.lines ?? []).map((line) => ({
        description: period ? `${line.description} — ${period}` : line.description,
        line_kind: "subscription" as LineKind,
        quantity: String(Number(line.quantity)),
        unit: "",
        unit_price: String(Number(line.unit_price)),
        tax_rate_id: defaultTaxRateId,
        ...(sub.period_end
          ? {
              subscription_id: sub.id,
              period_start: sub.period_start ?? undefined,
              period_end: sub.period_end,
            }
          : {}),
      })),
    );
  }

  /** dd-mm-jjjj, for the line **description** — it becomes stored document text, so it
   *  stays European and locale-independent (docs/UX.md) rather than the viewer's format. */
  function periodLabel(sub: BillableSubscription): string {
    const dmy = (iso: string) => iso.split("-").reverse().join("-");
    if (!sub.period_end) return "";
    return sub.period_start
      ? `${dmy(sub.period_start)} – ${dmy(sub.period_end)}`
      : dmy(sub.period_end);
  }

  /** The picker hint is transient UI, so it reads in the viewer's locale and year-aware. */
  function periodHint(sub: BillableSubscription): string {
    if (!sub.period_end) return "";
    return fmtPeriod(sub.period_start ?? sub.period_end, sub.period_end);
  }

  let productPick = $state("");
  let subscriptionPick = $state("");
  function removeLine(index: number) {
    lines = lines.filter((_, i) => i !== index);
  }

  const subscriptionItems = $derived(
    subscriptions.map((sub) => ({
      value: sub.id,
      label: sub.name,
      hint: [
        docMoney(Number(sub.amount), sub.currency || currency, locale),
        periodHint(sub),
        // Shown, never hidden: "did I already invoice March?" is answered here rather than
        // by a duplicate invoice.
        sub.already_billed ? t("invoicing.line.subscription_billed") : "",
      ]
        .filter(Boolean)
        .join(" · "),
    })),
  );

  const cellClass =
    "w-full rounded-lg border border-border bg-surface-raised px-2 py-1.5 text-sm outline-none focus:border-brand";
  const addClass = "text-sm font-medium text-brand hover:underline";
</script>

<div class="space-y-2">
  <!-- Header row (desktop); on mobile each line is its own labelled card row. -->
  <div
    class="hidden gap-2 text-xs font-semibold uppercase tracking-wide text-text-muted sm:grid sm:grid-cols-[1fr_7rem_4.5rem_4.5rem_6.5rem_8.5rem_6.5rem_2rem]"
  >
    <span>{t("invoicing.line.description")}</span>
    <span>{t("invoicing.line.kind_column")}</span>
    <span class="text-right">{t("invoicing.line.quantity")}</span>
    <span>{t("invoicing.line.unit")}</span>
    <span class="text-right">{t("invoicing.line.unit_price")}</span>
    <span>{t("invoicing.line.tax")}</span>
    <span class="text-right">{t("invoicing.line.amount")}</span>
    <span></span>
  </div>
  {#each lines as line, index (index)}
    <div
      class="grid grid-cols-2 items-center gap-2 rounded-lg border border-border p-2 sm:grid-cols-[1fr_7rem_4.5rem_4.5rem_6.5rem_8.5rem_6.5rem_2rem] sm:border-0 sm:p-0"
    >
      <input
        class="{cellClass} col-span-2 sm:col-span-1"
        placeholder={t("invoicing.line.description")}
        aria-label={t("invoicing.line.description")}
        bind:value={line.description}
      />
      <select
        class={cellClass}
        aria-label={t("invoicing.line.kind_column")}
        bind:value={line.line_kind}
      >
        {#each LINE_KINDS as kind (kind)}
          <option value={kind}>{lineKindLabel(kind)}</option>
        {/each}
      </select>
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
    <button type="button" class={addClass} onclick={() => addLine("product")}>
      ＋ {t("invoicing.line.add")}
    </button>
    <button type="button" class={addClass} onclick={() => addLine("hours")}>
      ＋ {t("invoicing.line.add_hours")}
    </button>
    {#if products.length > 0}
      <div class="w-52 min-w-0">
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
    {#if subscriptions.length > 0}
      <div class="w-56 min-w-0">
        <Combobox
          items={subscriptionItems}
          name="_subscription_pick"
          bind:value={subscriptionPick}
          id="line-subscription-pick"
          placeholder={t("invoicing.line.add_subscription")}
          onselect={addSubscription}
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
