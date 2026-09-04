<script lang="ts">
  /**
   * The document line editor (issue #207): **four sections** — Uren, Abonnementen, Domeinen,
   * Diensten — mirroring the bands the rendered document already prints (`lineSections`).
   *
   * It used to be one flat repeater with a kind `<select>` and a free `unit` box on every
   * row, which asked two questions that have no interesting answer. A line's *kind* is not a
   * per-row choice: it is which section you added it in, and the section is the thing that
   * knows where to get real data. A line's *unit* is a property of its kind for three of the
   * four — hours are hours, a recurring fee is one period, a renewal is one year — so it is
   * derived there and only typed on a service line, which genuinely sells things measured in
   * something.
   *
   * Domeinen split out of Abonnementen in #302. Both recur, but a register of forty renewals
   * is what an agency reconciles line by line against the registrar's own invoice, and it has
   * to be findable as a block rather than mixed in among three hosting retainers.
   *
   * The picked sections are **picked, not typed**: each opens `OutstandingPicker` over what
   * the client still owes, so the line arrives priced, dated, and carrying what it bills
   * (`time_entry_ids`, or the agreement/domain + period). That provenance travels back to the
   * API, which bills exactly those entries and claims exactly those periods — and is echoed
   * on read, so re-saving a draft cannot make it forget.
   *
   * Lines post as one JSON hidden field (`lines`) in **section order**, because the server
   * takes `position` from the array index and the document prints in that order. One save
   * button for the whole surface (docs/UX.md).
   */
  import { Plus, Trash2 } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";
  import Combobox from "$lib/core/ui/Combobox.svelte";

  import OutstandingPicker from "./OutstandingPicker.svelte";
  import { computePreview, lineKey, type EditableLine } from "./calc";
  import { LINE_KINDS, docMoney, lineKindLabel, periodText, taxRateLabel, unitFor } from "./types";
  import type {
    BillableDomain,
    BillableSubscription,
    LineKind,
    TaxRate,
    UnbilledEntry,
  } from "./types";

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
    hours = null,
    subscriptions = [],
    domains = [],
    outstandingLoading = false,
    defaultTaxRateId = "",
    defaultHourlyRate = "",
    currency,
    locale,
    pricesIncludeTax = false,
    formId,
    /** Quotes bill no hours and claim no period, so they get the sections without the pickers. */
    pickable = true,
  }: {
    lines: EditableLine[];
    taxRates: TaxRate[];
    /** The tenant's default service presets; empty hides the prefill picker. */
    products?: ProductPreset[];
    hours?: { entries: UnbilledEntry[]; truncated?: boolean; total_count?: number } | null;
    subscriptions?: BillableSubscription[];
    domains?: BillableDomain[];
    outstandingLoading?: boolean;
    defaultTaxRateId?: string;
    /** The org's default hourly rate, so a hand-typed hours line starts priced. */
    defaultHourlyRate?: string | number;
    currency: string;
    locale: string;
    pricesIncludeTax?: boolean;
    formId?: string;
    pickable?: boolean;
  } = $props();

  const activeRates = $derived(taxRates.filter((r) => r.active));
  const money = (value: number) => docMoney(value, currency, locale);
  const preview = $derived(
    computePreview(lines, taxRates, pricesIncludeTax, (rate) => taxRateLabel(rate, locale)),
  );

  /** Section order **is** document order: the server numbers `position` from this array and
   *  the paper prints Uren → Abonnementen → Diensten, so the two must not drift. */
  const ordered = $derived(LINE_KINDS.flatMap((kind) => lines.filter((l) => l.line_kind === kind)));

  const linesJson = $derived(
    JSON.stringify(
      ordered
        .filter((line) => line.description.trim())
        .map((line) => ({
          description: line.description.trim(),
          line_kind: line.line_kind,
          quantity: line.quantity || "1",
          unit: line.unit || unitFor(line.line_kind) || null,
          unit_price: line.unit_price || "0",
          tax_rate_id: line.tax_rate_id || null,
          ...(line.time_entry_ids?.length ? { time_entry_ids: line.time_entry_ids } : {}),
          // A claim only ever travels whole; the API refuses a half-claim, and a line may
          // carry one source, never both.
          ...(line.subscription_id && line.period_end
            ? {
                subscription_id: line.subscription_id,
                period_start: line.period_start ?? null,
                period_end: line.period_end,
              }
            : {}),
          ...(line.domain_id && line.period_end
            ? {
                domain_id: line.domain_id,
                period_start: line.period_start ?? null,
                period_end: line.period_end,
              }
            : {}),
        })),
    ),
  );

  function blank(kind: LineKind): EditableLine {
    return {
      key: lineKey(),
      description: "",
      line_kind: kind,
      quantity: "1",
      unit: unitFor(kind),
      unit_price: kind === "hours" ? String(Number(defaultHourlyRate || 0) || "") : "",
      tax_rate_id: defaultTaxRateId,
    };
  }

  /** Replace a still-empty trailing line of the same section instead of stacking under it. */
  function append(kind: LineKind, ...added: EditableLine[]) {
    const last = [...lines].reverse().find((l) => l.line_kind === kind);
    const base =
      last && !last.description.trim() && !last.unit_price
        ? lines.filter((l) => l !== last)
        : lines;
    lines = [...base, ...added];
  }

  function addLine(kind: LineKind) {
    append(kind, blank(kind));
  }

  /** A service preset **copies** onto a line: the line stays free text and snapshots what it
   *  copied, so re-pricing the preset never rewrites a document already written. */
  function addProduct(productId: string) {
    const product = products.find((p) => p.id === productId);
    productPick = "";
    if (!product) return;
    append("product", {
      key: lineKey(),
      description: product.description || product.name,
      line_kind: "product",
      quantity: "1",
      unit: product.unit ?? "",
      unit_price: String(Number(product.unit_price)),
      tax_rate_id: product.tax_rate_id || defaultTaxRateId,
    });
  }

  let productPick = $state("");
  //: Two pieces of state, not one nullable: the dialog owns `open` (it closes itself on
  //: Escape and on the backdrop), and `picking` must survive that close so the component is
  //: not re-created — and re-mounted mid-transition — every time it is dismissed.
  let pickerOpen = $state(false);
  let picking = $state<LineKind>("hours");

  function openPicker(kind: LineKind) {
    picking = kind;
    pickerOpen = true;
  }

  /** Turn the picker's ticks into lines. The ids are the picker's own encoding, decoded here
   *  so the dialog stays a list of priced rows and this stays the only place that knows what
   *  a line of each kind is made of. */
  function addPicked(ids: string[]) {
    const added: EditableLine[] = [];
    for (const id of ids) {
      const [prefix, ...rest] = id.split(":");
      if (prefix === "t") {
        const entry = hours?.entries.find((e) => e.id === rest[0]);
        if (!entry) continue;
        added.push({
          key: lineKey(),
          description:
            entry.description?.trim() ||
            entry.project_name ||
            t("invoicing.new.time_line_fallback"),
          line_kind: "hours",
          quantity: (entry.minutes / 60).toFixed(2),
          unit: unitFor("hours"),
          unit_price: String(Number(entry.rate)),
          tax_rate_id: defaultTaxRateId,
          time_entry_ids: [entry.id],
        });
        continue;
      }
      const [sourceId, periodEnd] = rest;
      const isAgreement = prefix === "s";
      const source = isAgreement
        ? subscriptions.find((s) => s.id === sourceId)
        : domains.find((d) => d.id === sourceId);
      const period = source?.periods?.find((p) => p.period_end === periodEnd);
      if (!source || !period) continue;
      const claim = isAgreement ? { subscription_id: source.id } : { domain_id: source.id };
      // An agreement's own lines become the document's, each keeping its own description —
      // "Hosting" and "Onderhoud" on one retainer stay two readable lines, not one lump.
      const offers = period.lines?.length
        ? period.lines
        : [{ description: source.name, quantity: "1", unit_price: String(period.amount) }];
      const span = periodText(period.period_start, period.period_end);
      for (const offer of offers) {
        added.push({
          key: lineKey(),
          // The span in parentheses, the shape the cron's own renewal line already has
          // ("Domeinverlenging klant.nl (01-01-2026-31-12-2026)"): a dash between a name and
          // a date range that is itself dashed reads as one run of dashes on paper.
          description: `${offer.description} (${span})`,
          // The kind follows the *source*, not the section the picker was opened from, so a
          // renewal is a renewal however it was reached.
          line_kind: isAgreement ? "subscription" : "domain",
          quantity: String(Number(offer.quantity)),
          unit: "",
          unit_price: String(Number(offer.unit_price)),
          tax_rate_id: defaultTaxRateId,
          ...claim,
          period_start: period.period_start ?? undefined,
          period_end: period.period_end,
        });
      }
    }
    if (added.length) lines = [...lines, ...added];
  }

  function removeLine(line: EditableLine) {
    lines = lines.filter((l) => l !== line);
  }

  /** How many pickable things each section still has waiting, for the badge on its button.
   *  Already-billed periods are listed by the picker but are not *waiting*, so they do not
   *  count — a badge that included them would never reach zero. */
  const hoursCount = $derived(hours?.total_count ?? 0);
  function openPeriods(rows: { periods?: { already_billed?: boolean }[] }[]): number {
    return rows.reduce(
      (n, row) => n + (row.periods ?? []).filter((p) => !p.already_billed).length,
      0,
    );
  }
  const subscriptionCount = $derived(openPeriods(subscriptions));
  const domainCount = $derived(openPeriods(domains));

  const PICK_COUNT: Record<string, () => number> = {
    hours: () => hoursCount,
    subscription: () => subscriptionCount,
    domain: () => domainCount,
  };

  /** Each picked section names what *it* offers: "Uren kiezen", "Periodes kiezen",
   *  "Verlengingen kiezen". One shared label would have to be vague enough to cover all
   *  three, which is exactly the button that teaches nobody where their line came from. */
  function pickLabel(kind: LineKind): string {
    if (kind === "hours") return t("invoicing.outstanding.pick_hours");
    if (kind === "domain") return t("invoicing.outstanding.pick_domains");
    return t("invoicing.outstanding.pick_subscriptions");
  }

  const SECTIONS = $derived(
    LINE_KINDS.map((kind) => ({
      kind,
      label: lineKindLabel(kind),
      rows: lines.filter((l) => l.line_kind === kind),
      /** Only a service line has a unit worth typing (stuks, dagen); the rest derive it. */
      showsUnit: kind === "product",
      count: PICK_COUNT[kind]?.() ?? 0,
    })),
  );

  /** Per-section subtotal, in entered terms — the same arithmetic the amount cell shows, so
   *  the two can never disagree. The document's own bands subtotal identically. */
  function sectionTotal(rows: EditableLine[]): number {
    return rows.reduce(
      (sum, line) => sum + Number(line.quantity || 0) * Number(line.unit_price || 0),
      0,
    );
  }

  const cellClass =
    "w-full rounded-lg border border-border bg-surface-raised px-2 py-1.5 text-sm outline-none focus:border-brand";
  const addClass = "inline-flex items-center gap-1 text-sm font-medium text-brand hover:underline";
  const gridWide = "sm:grid-cols-[1fr_4.5rem_4.5rem_6.5rem_8.5rem_6.5rem_2rem]";
  const gridNarrow = "sm:grid-cols-[1fr_4.5rem_6.5rem_8.5rem_6.5rem_2rem]";
</script>

<div class="space-y-5">
  {#each SECTIONS as section (section.kind)}
    {@const grid = section.showsUnit ? gridWide : gridNarrow}
    <section>
      <div class="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <h3 class="text-xs font-semibold uppercase tracking-wide text-text-muted">
          {section.label}
        </h3>
        {#if section.rows.length > 0}
          <span class="text-xs tabular-nums text-text-muted">
            {money(sectionTotal(section.rows))}
          </span>
        {/if}
      </div>

      {#if section.rows.length > 0}
        <!-- Column headings (desktop); on mobile each line is its own labelled card. -->
        <div
          class="hidden gap-2 pb-1 text-xs font-semibold uppercase tracking-wide text-text-muted sm:grid {grid}"
        >
          <span>{t("invoicing.line.description")}</span>
          {#if section.showsUnit}<span>{t("invoicing.line.unit")}</span>{/if}
          <span class="text-right">{t("invoicing.line.quantity")}</span>
          <span class="text-right">{t("invoicing.line.unit_price")}</span>
          <span>{t("invoicing.line.tax")}</span>
          <span class="text-right">{t("invoicing.line.amount")}</span>
          <span></span>
        </div>
        <div class="space-y-2">
          {#each section.rows as line (line.key)}
            <div
              class="grid grid-cols-2 items-center gap-2 rounded-lg border border-border p-2 {grid} sm:border-0 sm:p-0"
            >
              <input
                class="{cellClass} col-span-2 sm:col-span-1"
                placeholder={t("invoicing.line.description")}
                aria-label={t("invoicing.line.description")}
                bind:value={line.description}
              />
              {#if section.showsUnit}
                <input
                  class={cellClass}
                  placeholder={t("invoicing.line.unit")}
                  aria-label={t("invoicing.line.unit")}
                  bind:value={line.unit}
                />
              {/if}
              <input
                class="{cellClass} text-right"
                type="number"
                step="any"
                aria-label={t("invoicing.line.quantity")}
                bind:value={line.quantity}
              />
              <input
                class="{cellClass} text-right"
                type="number"
                step="any"
                aria-label={t("invoicing.line.unit_price")}
                bind:value={line.unit_price}
              />
              <select
                class={cellClass}
                aria-label={t("invoicing.line.tax")}
                bind:value={line.tax_rate_id}
              >
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
                onclick={() => removeLine(line)}
              >
                <Trash2 size={15} />
              </button>
            </div>
          {/each}
        </div>
      {/if}

      <div class="mt-2 flex flex-wrap items-center gap-3">
        {#if section.kind !== "product" && pickable}
          <button type="button" class={addClass} onclick={() => openPicker(section.kind)}>
            <Plus size={14} />
            {pickLabel(section.kind)}
            {#if section.count > 0}
              <span
                class="rounded-full bg-brand/10 px-1.5 py-0.5 text-xs font-semibold tabular-nums text-brand"
              >
                {section.count}
              </span>
            {/if}
          </button>
        {/if}
        <button type="button" class={addClass} onclick={() => addLine(section.kind)}>
          <Plus size={14} />
          {t("invoicing.line.add")}
        </button>
        {#if section.kind === "product" && products.length > 0}
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
              keepOpenOnSelect
            />
          </div>
        {/if}
      </div>
    </section>
  {/each}

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

{#if pickable}
  <OutstandingPicker
    bind:open={pickerOpen}
    kind={picking}
    {hours}
    {subscriptions}
    {domains}
    {currency}
    {locale}
    loading={outstandingLoading}
    onadd={addPicked}
  />
{/if}
