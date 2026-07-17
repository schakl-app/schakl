import type { components } from "$lib/core/api/schema";

export type Invoice = components["schemas"]["InvoiceRead"];
export type Quote = components["schemas"]["QuoteRead"];
export type InvoiceLine = components["schemas"]["LineRead"];
export type TaxRate = components["schemas"]["TaxRateRead"];
// FastAPI prefixes on name collision (the tasks module also has a TemplateRead).
export type DocTemplate = components["schemas"]["app__modules__invoicing__schemas__TemplateRead"];
export type InvoicingSettings = components["schemas"]["InvoicingSettingsRead"];
export type SellerDetails = components["schemas"]["SellerDetails"];

/** A tax rate's display label in the UI locale — tenant data, like subscription types. */
export function taxRateLabel(rate: TaxRate | undefined, locale: string): string {
  if (!rate) return "—";
  const labels = (rate.label_i18n ?? {}) as Record<string, string>;
  return labels[locale] || labels.en || labels.nl || Object.values(labels)[0] || `${rate.rate}%`;
}

/** Per-locale template text with the same fallback chain the API's tax labels use. */
export function templateText(block: Record<string, string> | undefined, locale: string): string {
  if (!block) return "";
  return block[locale] || block.en || block.nl || Object.values(block)[0] || "";
}

/** Money in the *document's* currency — a document may deviate from the org currency. */
export function docMoney(
  value: string | number | null | undefined,
  currency: string,
  locale: string,
): string {
  if (value == null || value === "") return "—";
  return new Intl.NumberFormat(locale === "nl" ? "nl-NL" : "en-GB", {
    style: "currency",
    currency: currency || "EUR",
  }).format(Number(value));
}
