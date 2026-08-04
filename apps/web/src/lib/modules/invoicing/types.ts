import { t } from "$lib/core/i18n";

import type { components } from "$lib/core/api/schema";

export type Invoice = components["schemas"]["InvoiceRead"];
export type Quote = components["schemas"]["QuoteRead"];
export type InvoiceLine = components["schemas"]["LineRead"];
export type TaxRate = components["schemas"]["TaxRateRead"];
// FastAPI prefixes on name collision (the tasks module also has a TemplateRead).
export type DocTemplate = components["schemas"]["app__modules__invoicing__schemas__TemplateRead"];
export type InvoicingSettings = components["schemas"]["InvoicingSettingsRead"];
export type SellerDetails = components["schemas"]["SellerDetails"];
export type LineKind = components["schemas"]["LineKind"];
export type BillableSubscription = components["schemas"]["BillableSubscription"];
export type BillableDomain = components["schemas"]["BillableDomain"];
export type PeriodOffer = components["schemas"]["PeriodOffer"];
export type Outstanding = components["schemas"]["OutstandingRead"];
export type UnbilledEntry = components["schemas"]["UnbilledEntry"];
export type AutoInvoiceMode = components["schemas"]["AutoInvoiceMode"];

/** The order the three kinds appear in: what was worked, what recurs, what was sold. */
export const LINE_KINDS = ["hours", "subscription", "product"] as const satisfies LineKind[];

/** The automation levels, weakest first — each contains the one before it. */
export const AUTO_INVOICE_MODES = [
  "off",
  "draft",
  "issue",
  "send",
] as const satisfies AutoInvoiceMode[];

/**
 * Read the automation level a form posted, as the API's own vocabulary.
 *
 * `""` is the *inherit* choice and becomes `null` — a third state meaning "follow the org",
 * which is not the same as any level and must reach the API as an explicit null. An
 * unrecognised value is also `null` rather than a guess: a tampered form should fall back to
 * inheriting, never to a level nobody chose.
 */
export function readAutoInvoiceMode(value: FormDataEntryValue | null): AutoInvoiceMode | null {
  const raw = String(value ?? "").trim();
  return (AUTO_INVOICE_MODES as readonly string[]).includes(raw)
    ? (raw as AutoInvoiceMode)
    : null;
}

export function lineKindLabel(kind: LineKind): string {
  return t(`invoicing.line.kind.${kind}`);
}

/**
 * The **document's** unit for a line of this kind, or "" when the line names its own.
 *
 * Hours are hours: asking the user to type "uur" into every hours line was a field that could
 * only ever be filled in one way, or wrong. A recurring line's quantity is the agreement's
 * own (1 × the monthly fee), so it needs no unit either. Only a service line sells things
 * measured in something — stuks, dagen, woorden — and there the field stays.
 */
export function unitFor(kind: LineKind): string {
  return kind === "hours" ? t("invoicing.unit.hour") : "";
}

/** dd-mm-jjjj for text that gets **stored on the document** — European and locale-independent
 *  (docs/UX.md), never the viewer's format: it becomes the line the client reads. */
export function periodText(start: string | null | undefined, end: string): string {
  const dmy = (iso: string) => iso.split("-").reverse().join("-");
  return start ? `${dmy(start)} – ${dmy(end)}` : dmy(end);
}

/**
 * Lines grouped into their kinds, each keeping its own order — mirrors `_sections()` in
 * `apps/api/app/modules/invoicing/pdf.py`. A document whose lines are all one kind gets a
 * single unlabelled group: a lone "UREN" band subtotalling to the subtotal beneath it is
 * noise, and headers earn their place exactly when two kinds must be told apart.
 */
export function lineSections<T extends { line_kind?: LineKind | null }>(
  lines: T[],
): { kind: LineKind | ""; label: string; lines: T[] }[] {
  const buckets = new Map<LineKind, T[]>();
  for (const line of lines) {
    const kind = (line.line_kind ?? "product") as LineKind;
    const key = LINE_KINDS.includes(kind) ? kind : "product";
    buckets.set(key, [...(buckets.get(key) ?? []), line]);
  }
  const ordered = LINE_KINDS.filter((kind) => buckets.has(kind));
  if (ordered.length <= 1) return [{ kind: "", label: "", lines }];
  return ordered.map((kind) => ({
    kind,
    label: lineKindLabel(kind),
    lines: buckets.get(kind) ?? [],
  }));
}

function hexToRgb(hex: string): [number, number, number] {
  const clean = hex.length === 4 ? hex.replace(/[0-9a-f]/gi, (c) => c + c) : hex;
  const n = parseInt(clean.slice(1, 7), 16);
  return Number.isNaN(n) ? [79, 70, 229] : [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function luminance([r, g, b]: [number, number, number]): number {
  const chan = (v: number) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b);
}

/**
 * The tenant's colour, darkened until it reads on paper — hue preserved.
 *
 * The document is white whatever the app theme, so this is `deriveOnDark` from
 * `lib/core/theme.ts` pointed the other way: a pale-yellow or mint brand would otherwise
 * render an invisible heading and an unreadable section band. 4.5:1 because the accent
 * carries *small* text (section labels, the total), not only rules. **Keep in sync with
 * `document_accent()` in `apps/api/app/modules/invoicing/pdf.py`** — preview and PDF must
 * reach the same colour, or the document changes appearance on download.
 */
export function documentAccent(hex: string | null | undefined): string {
  const rgb = hexToRgb(hex || "#4f46e5");
  if (1.05 / (luminance(rgb) + 0.05) >= 4.5) return hex || "#4f46e5";
  const [r, g, b] = rgb.map((v) => v / 255) as [number, number, number];
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  let l = (max + min) / 2;
  const d = max - min;
  const s = d === 0 ? 0 : l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h = 0;
  if (d !== 0) {
    if (max === r) h = ((g - b) / d + (g < b ? 6 : 0)) / 6;
    else if (max === g) h = ((b - r) / d + 2) / 6;
    else h = ((r - g) / d + 4) / 6;
  }
  const toRgb = (): [number, number, number] => {
    if (s === 0) return [l * 255, l * 255, l * 255];
    const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
    const p = 2 * l - q;
    const chan = (tt: number) => {
      let x = tt;
      if (x < 0) x += 1;
      if (x > 1) x -= 1;
      if (x < 1 / 6) return p + (q - p) * 6 * x;
      if (x < 1 / 2) return q;
      if (x < 2 / 3) return p + (q - p) * (2 / 3 - x) * 6;
      return p;
    };
    return [chan(h + 1 / 3) * 255, chan(h) * 255, chan(h - 1 / 3) * 255];
  };
  let out = rgb;
  for (let i = 0; i < 24 && 1.05 / (luminance(out) + 0.05) < 4.5; i++) {
    l = Math.max(l - 0.04, 0.08);
    out = toRgb().map(Math.round) as [number, number, number];
  }
  return `#${out.map((v) => v.toString(16).padStart(2, "0")).join("")}`;
}

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
