<script lang="ts">
  /**
   * The rendered document (issue #207) — one component for the detail preview and the
   * print page, styled by the tenant's template.
   *
   * It is the **matched pair** of `apps/api/app/modules/invoicing/pdf.py`: same blocks in
   * the same order, the same palette, the same grouping into uren / abonnementen /
   * diensten, the same contrast-corrected accent. Change one, change the other — a client
   * who reads the preview and then opens the PDF must not see two different documents.
   *
   * Branding is runtime (Golden Rule 4): the accent falls back to the tenant's brand
   * colour, the logo to the tenant logo. All texts render in the **document's** locale, not
   * the viewer's. Paper is white whatever the app theme, so the ink is pinned to the light
   * values of `app.css`'s tokens rather than reading the theme variables.
   */
  import { t } from "$lib/core/i18n";
  import { formatPhone } from "$lib/core/phone";
  import Markdown from "$lib/core/ui/Markdown.svelte";
  import { docMoney, documentAccent, lineSections, templateText } from "./types";
  import type { DocTemplate, Invoice, Quote, SellerDetails } from "./types";

  let {
    doc,
    kind,
    template = null,
    seller = {},
    brandName = "",
    logoUrl = null,
    brandColor = "#4f46e5",
  }: {
    doc: Invoice | Quote;
    kind: "invoice" | "quote";
    template?: DocTemplate | null;
    seller?: SellerDetails | Record<string, never>;
    brandName?: string;
    logoUrl?: string | null;
    brandColor?: string;
  } = $props();

  interface TemplateConfigShape {
    accent_color?: string | null;
    show_logo?: boolean;
    columns?: Partial<Record<"quantity" | "unit" | "unit_price" | "tax", boolean>>;
    intro_i18n?: Record<string, string>;
    payment_i18n?: Record<string, string>;
    footer_i18n?: Record<string, string>;
  }
  const config = $derived((template?.config ?? {}) as TemplateConfigShape);
  const columns = $derived({
    quantity: true,
    unit: false,
    unit_price: true,
    tax: true,
    ...(config.columns ?? {}),
  });
  const accent = $derived(documentAccent(config.accent_color || brandColor));
  const showLogo = $derived(config.show_logo !== false);
  const locale = $derived(doc.locale || "nl");
  const money = (value: string | number | null | undefined) =>
    docMoney(value, doc.currency, locale);
  const dmy = (iso: string | null | undefined) => (iso ? iso.split("-").reverse().join("-") : "—");

  const invoice = $derived(kind === "invoice" ? (doc as Invoice) : null);
  const heading = $derived(
    kind === "quote"
      ? t("invoicing.doc.quote")
      : invoice?.kind === "credit_note"
        ? t("invoicing.doc.credit_note")
        : t("invoicing.doc.invoice"),
  );
  const watermark = $derived(
    doc.status === "draft"
      ? t("invoicing.doc.draft_watermark")
      : doc.status === "cancelled"
        ? t("invoicing.doc.cancelled_watermark")
        : "",
  );
  const customer = $derived((doc.customer ?? {}) as Record<string, string | null>);
  const intro = $derived(doc.intro || templateText(config.intro_i18n, locale));
  const paymentText = $derived(templateText(config.payment_i18n, locale));
  const footerText = $derived(templateText(config.footer_i18n, locale));
  const lines = $derived(doc.lines ?? []);
  const sections = $derived(lineSections(lines));
  const grouped = $derived(sections.length > 1);
  const hasReverseCharge = $derived(lines.some((line) => line.tax_category === "reverse_charge"));
  const sectionTotal = (rows: typeof lines) =>
    rows.reduce((sum, line) => sum + Number(line.amount ?? 0), 0);
  /** Columns before the amount, so a section's subtotal row can span them. */
  const leadingColumns = $derived(
    1 + [columns.quantity, columns.unit, columns.unit_price, columns.tax].filter(Boolean).length,
  );
</script>

<article
  class="relative mx-auto w-full max-w-3xl rounded-xl border border-border bg-white p-8 text-[#171717] shadow-sm print:max-w-none print:rounded-none print:border-0 print:p-0 print:shadow-none sm:p-10"
>
  {#if watermark}
    <div
      class="pointer-events-none absolute inset-0 flex items-center justify-center overflow-hidden"
      aria-hidden="true"
    >
      <span class="rotate-[-24deg] text-7xl font-black tracking-widest text-[#eeeeee]"
        >{watermark}</span
      >
    </div>
  {/if}

  <header class="relative mb-6 flex flex-wrap items-start justify-between gap-6">
    <div class="min-w-0">
      {#if showLogo && logoUrl}
        <img src={logoUrl} alt={brandName} class="mb-4 h-12 w-auto max-w-[13rem] object-contain" />
      {/if}
      <h1 class="text-3xl font-bold tracking-tight" style="color: {accent}">{heading}</h1>
      {#if doc.number}
        <p class="mt-1 text-sm text-[#737373]">{doc.number}</p>
      {/if}
    </div>
    <div class="shrink-0 text-right text-[0.8125rem] leading-[1.45] text-[#737373]">
      <p class="font-semibold text-[#171717]">{seller.name || brandName}</p>
      {#if seller.address_line1}<p>{seller.address_line1}</p>{/if}
      {#if seller.address_line2}<p>{seller.address_line2}</p>{/if}
      {#if seller.postal_code || seller.city}
        <p>{[seller.postal_code, seller.city].filter(Boolean).join(" ")}</p>
      {/if}
      {#if seller.vat_number}<p>{t("invoicing.doc.vat_number")} {seller.vat_number}</p>{/if}
      {#if seller.coc_number}<p>{t("invoicing.doc.coc_number")} {seller.coc_number}</p>{/if}
      {#if seller.iban}<p>{t("invoicing.doc.iban")} {seller.iban}</p>{/if}
      {#if seller.email}<p>{seller.email}</p>{/if}
      {#if seller.phone}<p>{formatPhone(seller.phone)}</p>{/if}
    </div>
  </header>

  <hr class="relative mb-6 border-t-2" style="border-color: {accent}" />

  <div class="relative mb-7 flex flex-wrap items-start justify-between gap-x-6 gap-y-5">
    <div class="text-[0.9375rem] leading-[1.5]">
      <p class="mb-1 text-[0.6875rem] font-semibold uppercase tracking-wider text-[#737373]">
        {t("invoicing.doc.bill_to")}
      </p>
      <p class="font-semibold">{customer.name || "—"}</p>
      <div class="text-[#737373]">
        {#if customer.address_line1}<p>{customer.address_line1}</p>{/if}
        {#if customer.address_line2}<p>{customer.address_line2}</p>{/if}
        {#if customer.postal_code || customer.city}
          <p>{[customer.postal_code, customer.city].filter(Boolean).join(" ")}</p>
        {/if}
        <!-- Country only when it differs from ours: a domestic invoice needn't state "NL". -->
        {#if customer.country && customer.country !== seller.country}
          <p>{customer.country}</p>
        {/if}
        {#if customer.vat_number}
          <p>{t("invoicing.doc.vat_number")} {customer.vat_number}</p>
        {/if}
        {#if customer.coc_number}
          <p>{t("invoicing.doc.coc_number")} {customer.coc_number}</p>
        {/if}
        {#if customer.email}<p>{customer.email}</p>{/if}
      </div>
    </div>
    <dl class="grid grid-cols-[auto_auto] gap-x-8 gap-y-[0.3rem] text-sm">
      <dt class="text-[#737373]">
        {kind === "quote" ? t("invoicing.doc.quote_number") : t("invoicing.doc.number")}
      </dt>
      <dd class="text-right font-semibold">{doc.number ?? "—"}</dd>
      <dt class="text-[#737373]">{t("invoicing.doc.date")}</dt>
      <dd class="text-right font-semibold">{dmy(doc.issue_date)}</dd>
      {#if kind === "invoice"}
        <dt class="text-[#737373]">{t("invoicing.doc.due")}</dt>
        <dd class="text-right font-semibold">{dmy(invoice?.due_date)}</dd>
      {:else}
        <dt class="text-[#737373]">{t("invoicing.doc.valid_until")}</dt>
        <dd class="text-right font-semibold">{dmy((doc as Quote).valid_until)}</dd>
      {/if}
      {#if doc.reference}
        <dt class="text-[#737373]">{t("invoicing.doc.reference")}</dt>
        <dd class="text-right font-semibold">{doc.reference}</dd>
      {/if}
      {#if invoice?.period_end}
        <dt class="text-[#737373]">{t("invoicing.doc.period")}</dt>
        <dd class="text-right font-semibold">
          {invoice.period_start ? `${dmy(invoice.period_start)} – ` : ""}{dmy(invoice.period_end)}
        </dd>
      {/if}
    </dl>
  </div>

  {#if intro}
    <p class="relative mb-6 whitespace-pre-line text-[0.9375rem] leading-relaxed">{intro}</p>
  {/if}

  <!-- Wide tables scroll inside their own container; the page body never scrolls sideways. -->
  <div class="relative -mx-1 overflow-x-auto px-1">
    <table class="w-full min-w-[34rem] text-[0.9375rem]">
      <thead>
        <tr class="text-left">
          <th
            class="border-b-2 pb-1.5 pr-3 text-[0.6875rem] font-semibold uppercase tracking-wider text-[#737373]"
            style="border-color: {accent}">{t("invoicing.line.description")}</th
          >
          {#if columns.quantity}
            <th
              class="w-16 border-b-2 pb-1.5 pr-3 text-right text-[0.6875rem] font-semibold uppercase tracking-wider text-[#737373]"
              style="border-color: {accent}">{t("invoicing.line.quantity")}</th
            >
          {/if}
          {#if columns.unit}
            <th
              class="w-20 border-b-2 pb-1.5 pr-3 text-[0.6875rem] font-semibold uppercase tracking-wider text-[#737373]"
              style="border-color: {accent}">{t("invoicing.line.unit")}</th
            >
          {/if}
          {#if columns.unit_price}
            <th
              class="w-28 border-b-2 pb-1.5 pr-3 text-right text-[0.6875rem] font-semibold uppercase tracking-wider text-[#737373]"
              style="border-color: {accent}">{t("invoicing.line.unit_price")}</th
            >
          {/if}
          {#if columns.tax}
            <th
              class="w-24 border-b-2 pb-1.5 pr-3 text-right text-[0.6875rem] font-semibold uppercase tracking-wider text-[#737373]"
              style="border-color: {accent}">{t("invoicing.line.tax")}</th
            >
          {/if}
          <th
            class="w-28 border-b-2 pb-1.5 text-right text-[0.6875rem] font-semibold uppercase tracking-wider text-[#737373]"
            style="border-color: {accent}">{t("invoicing.line.amount")}</th
          >
        </tr>
      </thead>
      {#each sections as section (section.kind)}
        <tbody>
          {#if grouped}
            <tr>
              <td
                colspan={leadingColumns + 1}
                class="px-2 py-1.5 text-[0.6875rem] font-semibold uppercase tracking-wider"
                style="color: {accent}; background-color: color-mix(in srgb, {accent} 11%, white)"
              >
                {section.label}
              </td>
            </tr>
          {/if}
          {#each section.lines as line (line.id)}
            <tr class="border-b border-[#e5e5e5] align-top">
              <td class="py-2.5 pr-3">{line.description}</td>
              {#if columns.quantity}
                <td class="py-2.5 pr-3 text-right tabular-nums">{Number(line.quantity)}</td>
              {/if}
              {#if columns.unit}
                <td class="py-2.5 pr-3 text-[#737373]">{line.unit ?? ""}</td>
              {/if}
              {#if columns.unit_price}
                <td class="py-2.5 pr-3 text-right tabular-nums">{money(line.unit_price)}</td>
              {/if}
              {#if columns.tax}
                <td class="py-2.5 pr-3 text-right text-sm tabular-nums text-[#737373]">
                  {line.tax_name || `${Number(line.tax_rate_pct)}%`}
                </td>
              {/if}
              <td class="py-2.5 text-right tabular-nums">{money(line.amount)}</td>
            </tr>
          {/each}
          {#if grouped}
            <tr>
              <td colspan={leadingColumns} class="py-1.5 pr-3 text-right text-sm text-[#737373]">
                {t("invoicing.doc.section_subtotal", { section: section.label })}
              </td>
              <td class="py-1.5 text-right text-sm font-semibold tabular-nums">
                {money(sectionTotal(section.lines))}
              </td>
            </tr>
          {/if}
        </tbody>
      {/each}
    </table>
  </div>

  <div class="relative mt-5 flex justify-end">
    <dl class="w-full max-w-[19rem] rounded-lg bg-[#fafafa] px-4 py-3 text-sm">
      <div class="flex justify-between py-[0.2rem]">
        <dt class="text-[#737373]">{t("invoicing.doc.subtotal")}</dt>
        <dd class="tabular-nums">{money(doc.subtotal)}</dd>
      </div>
      {#each doc.tax_groups ?? [] as group (group.name + group.rate_pct)}
        <div class="flex justify-between py-[0.2rem]">
          <dt class="text-[#737373]">{group.name || `${Number(group.rate_pct)}%`}</dt>
          <dd class="tabular-nums">{money(group.tax)}</dd>
        </div>
      {/each}
      <div class="mt-1 flex justify-between border-t border-[#e5e5e5] pt-2 text-base font-semibold">
        <dt>{t("invoicing.doc.total")}</dt>
        <dd class="tabular-nums" style="color: {accent}">{money(doc.total)}</dd>
      </div>
      {#if invoice && Number(invoice.paid_total) !== 0}
        <div class="flex justify-between py-[0.2rem]">
          <dt class="text-[#737373]">{t("invoicing.doc.paid")}</dt>
          <dd class="tabular-nums">{money(invoice.paid_total)}</dd>
        </div>
        <div class="flex justify-between border-t border-[#e5e5e5] pt-2 font-semibold">
          <dt>{t("invoicing.doc.to_pay")}</dt>
          <dd class="tabular-nums" style="color: {accent}">{money(invoice.outstanding)}</dd>
        </div>
      {/if}
    </dl>
  </div>

  {#if hasReverseCharge}
    <p class="relative mt-5 text-xs text-[#737373]">
      {t("settings.invoicing.category.reverse_charge")}
    </p>
  {/if}
  {#if doc.notes}
    <!-- Notes are markdown (#228). The document is paper — white with fixed ink whatever the
         app theme — so pin the variables Markdown's styles read to the document palette. -->
    <div
      class="relative mt-6 text-[0.9375rem]"
      style="--color-text: #171717; --color-brand: {accent}; --color-border: #e5e5e5; --color-surface: #fafafa"
    >
      <Markdown value={doc.notes} />
    </div>
  {/if}
  {#if paymentText}
    <p
      class="relative mt-6 whitespace-pre-line border-l-[3px] pl-3.5 text-[0.9375rem]"
      style="border-color: {accent}"
    >
      {paymentText}
    </p>
  {:else if kind === "invoice" && invoice && seller.iban && invoice.kind !== "credit_note"}
    <!-- No template payment text configured: an invoice still states how to pay (owner
         feedback) — total, deadline, account, reference. -->
    <p class="relative mt-6 border-l-[3px] pl-3.5 text-[0.9375rem]" style="border-color: {accent}">
      {t("invoicing.doc.payment_fallback", {
        total: money(invoice.outstanding ?? doc.total),
        due: dmy(invoice.due_date),
        iban: seller.iban,
        number: doc.number ?? heading,
      })}
    </p>
  {/if}
  {#if footerText}
    <p class="relative mt-8 border-t border-[#e5e5e5] pt-3 text-center text-xs text-[#737373]">
      {footerText}
    </p>
  {/if}
</article>
