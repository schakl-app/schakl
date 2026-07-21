<script lang="ts">
  /**
   * The rendered document (issue #207) — one component for the detail preview and the
   * print page, styled by the tenant's template. Branding is runtime (Golden Rule 4):
   * the accent falls back to the brand color, the logo to the tenant logo. All texts
   * render in the **document's** locale, not the viewer's.
   */
  import { t } from "$lib/core/i18n";
  import Markdown from "$lib/core/ui/Markdown.svelte";
  import { docMoney, templateText } from "./types";
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
  const accent = $derived(config.accent_color || brandColor);
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
  const hasReverseCharge = $derived(
    (doc.lines ?? []).some((line) => line.tax_category === "reverse_charge"),
  );
</script>

<article
  class="relative mx-auto w-full max-w-3xl rounded-xl border border-border bg-white p-8 text-gray-900 shadow-sm print:max-w-none print:rounded-none print:border-0 print:p-0 print:shadow-none"
>
  {#if watermark}
    <div
      class="pointer-events-none absolute inset-0 flex items-center justify-center"
      aria-hidden="true"
    >
      <span class="rotate-[-24deg] text-7xl font-black tracking-widest text-gray-200"
        >{watermark}</span
      >
    </div>
  {/if}

  <header class="mb-8 flex items-start justify-between gap-6">
    <div class="min-w-0">
      {#if showLogo && logoUrl}
        <img src={logoUrl} alt={brandName} class="mb-3 h-12 w-auto object-contain" />
      {/if}
      <h1 class="text-2xl font-bold tracking-wide" style="color: {accent}">{heading}</h1>
      {#if doc.number}
        <p class="mt-1 font-mono text-sm text-gray-600">{doc.number}</p>
      {/if}
    </div>
    <div class="shrink-0 text-right text-sm leading-relaxed text-gray-700">
      <p class="font-semibold text-gray-900">{seller.name || brandName}</p>
      {#if seller.address_line1}<p>{seller.address_line1}</p>{/if}
      {#if seller.address_line2}<p>{seller.address_line2}</p>{/if}
      {#if seller.postal_code || seller.city}
        <p>{[seller.postal_code, seller.city].filter(Boolean).join(" ")}</p>
      {/if}
      {#if seller.vat_number}
        <p class="mt-1">{t("invoicing.doc.vat_number")} {seller.vat_number}</p>
      {/if}
      {#if seller.coc_number}<p>{t("invoicing.doc.coc_number")} {seller.coc_number}</p>{/if}
      {#if seller.iban}<p>{t("invoicing.doc.iban")} {seller.iban}</p>{/if}
      {#if seller.email}<p>{seller.email}</p>{/if}
      {#if seller.phone}<p>{seller.phone}</p>{/if}
    </div>
  </header>

  <div class="mb-8 flex flex-wrap items-end justify-between gap-6">
    <div class="text-sm leading-relaxed">
      <p class="text-xs font-semibold uppercase tracking-wide text-gray-500">
        {t("invoicing.doc.bill_to")}
      </p>
      <p class="mt-1 font-semibold">{customer.name || "—"}</p>
      {#if customer.address_line1}<p>{customer.address_line1}</p>{/if}
      {#if customer.address_line2}<p>{customer.address_line2}</p>{/if}
      {#if customer.postal_code || customer.city}
        <p>{[customer.postal_code, customer.city].filter(Boolean).join(" ")}</p>
      {/if}
      {#if customer.vat_number}
        <p class="mt-1 text-gray-600">
          {t("invoicing.doc.vat_number")}
          {customer.vat_number}
        </p>
      {/if}
      {#if customer.coc_number}
        <p class="text-gray-600">{t("invoicing.doc.coc_number")} {customer.coc_number}</p>
      {/if}
      {#if customer.email}<p class="text-gray-600">{customer.email}</p>{/if}
    </div>
    <dl class="grid grid-cols-2 gap-x-6 gap-y-1 text-sm">
      <dt class="text-gray-500">
        {kind === "quote" ? t("invoicing.doc.quote_number") : t("invoicing.doc.number")}
      </dt>
      <dd class="text-right font-medium">{doc.number ?? "—"}</dd>
      <dt class="text-gray-500">{t("invoicing.doc.date")}</dt>
      <dd class="text-right font-medium">{dmy(doc.issue_date)}</dd>
      {#if kind === "invoice"}
        <dt class="text-gray-500">{t("invoicing.doc.due")}</dt>
        <dd class="text-right font-medium">{dmy(invoice?.due_date)}</dd>
      {:else}
        <dt class="text-gray-500">{t("invoicing.doc.valid_until")}</dt>
        <dd class="text-right font-medium">{dmy((doc as Quote).valid_until)}</dd>
      {/if}
      {#if doc.reference}
        <dt class="text-gray-500">{t("invoicing.doc.reference")}</dt>
        <dd class="text-right font-medium">{doc.reference}</dd>
      {/if}
    </dl>
  </div>

  {#if intro}
    <p class="mb-6 whitespace-pre-line text-sm leading-relaxed text-gray-700">{intro}</p>
  {/if}

  <table class="w-full text-sm">
    <thead>
      <tr class="border-b-2 text-left" style="border-color: {accent}">
        <th class="py-2 pr-3 font-semibold">{t("invoicing.line.description")}</th>
        {#if columns.quantity}
          <th class="w-20 py-2 pr-3 text-right font-semibold">{t("invoicing.line.quantity")}</th>
        {/if}
        {#if columns.unit}
          <th class="w-20 py-2 pr-3 text-left font-semibold">{t("invoicing.line.unit")}</th>
        {/if}
        {#if columns.unit_price}
          <th class="w-28 py-2 pr-3 text-right font-semibold">{t("invoicing.line.unit_price")}</th>
        {/if}
        {#if columns.tax}
          <th class="w-24 py-2 pr-3 text-right font-semibold">{t("invoicing.line.tax")}</th>
        {/if}
        <th class="w-28 py-2 text-right font-semibold">{t("invoicing.line.amount")}</th>
      </tr>
    </thead>
    <tbody>
      {#each doc.lines ?? [] as line (line.id)}
        <tr class="border-b border-gray-200 align-top">
          <td class="py-2 pr-3">{line.description}</td>
          {#if columns.quantity}
            <td class="py-2 pr-3 text-right tabular-nums">{Number(line.quantity)}</td>
          {/if}
          {#if columns.unit}
            <td class="py-2 pr-3">{line.unit ?? ""}</td>
          {/if}
          {#if columns.unit_price}
            <td class="py-2 pr-3 text-right tabular-nums">{money(line.unit_price)}</td>
          {/if}
          {#if columns.tax}
            <td class="py-2 pr-3 text-right tabular-nums text-gray-600">
              {line.tax_name || `${Number(line.tax_rate_pct)}%`}
            </td>
          {/if}
          <td class="py-2 text-right tabular-nums">{money(line.amount)}</td>
        </tr>
      {/each}
    </tbody>
  </table>

  <div class="mt-4 flex justify-end">
    <dl class="w-72 space-y-1 text-sm">
      <div class="flex justify-between">
        <dt class="text-gray-500">{t("invoicing.doc.subtotal")}</dt>
        <dd class="tabular-nums">{money(doc.subtotal)}</dd>
      </div>
      {#each doc.tax_groups ?? [] as group (group.name + group.rate_pct)}
        <div class="flex justify-between">
          <dt class="text-gray-500">
            {group.name || `${Number(group.rate_pct)}%`}
          </dt>
          <dd class="tabular-nums">{money(group.tax)}</dd>
        </div>
      {/each}
      <div
        class="flex justify-between border-t-2 pt-1 text-base font-semibold"
        style="border-color: {accent}"
      >
        <dt>{t("invoicing.doc.total")}</dt>
        <dd class="tabular-nums">{money(doc.total)}</dd>
      </div>
      {#if invoice && Number(invoice.paid_total) !== 0}
        <div class="flex justify-between">
          <dt class="text-gray-500">{t("invoicing.doc.paid")}</dt>
          <dd class="tabular-nums">{money(invoice.paid_total)}</dd>
        </div>
        <div class="flex justify-between font-semibold">
          <dt>{t("invoicing.doc.to_pay")}</dt>
          <dd class="tabular-nums">{money(invoice.outstanding)}</dd>
        </div>
      {/if}
    </dl>
  </div>

  {#if hasReverseCharge}
    <p class="mt-4 text-xs text-gray-600">{t("settings.invoicing.category.reverse_charge")}</p>
  {/if}
  {#if doc.notes}
    <!-- Notes are markdown (#228). The document is paper — white with fixed ink whatever the
         app theme — so pin the variables Markdown's styles read to the document palette. -->
    <div
      class="mt-6 text-sm"
      style="--color-text: #374151; --color-brand: {accent}; --color-border: #e5e7eb; --color-surface: #f9fafb"
    >
      <Markdown value={doc.notes} />
    </div>
  {/if}
  {#if paymentText}
    <p class="mt-6 whitespace-pre-line text-sm text-gray-700">{paymentText}</p>
  {:else if kind === "invoice" && invoice && seller.iban && invoice.kind !== "credit_note"}
    <!-- No template payment text configured: an invoice still states how to pay (owner
         feedback) — total, deadline, account, reference. -->
    <p class="mt-6 text-sm text-gray-700">
      {t("invoicing.doc.payment_fallback", {
        total: money(invoice.outstanding ?? doc.total),
        due: dmy(invoice.due_date),
        iban: seller.iban,
        number: doc.number ?? heading,
      })}
    </p>
  {/if}
  {#if footerText}
    <p class="mt-8 border-t border-gray-200 pt-3 text-center text-xs text-gray-500">
      {footerText}
    </p>
  {/if}
</article>
