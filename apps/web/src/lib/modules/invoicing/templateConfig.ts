/**
 * The template config, as the editor holds it.
 *
 * `mergeLayout` mirrors `resolve_layout()` in `apps/api/app/modules/invoicing/render/blocks.py`,
 * and the two must agree: the editor draws what it thinks the document will print, so a merge
 * that ordered blocks differently would show a preview of a layout nobody asked for. The
 * *rendered* preview beside the editor comes from the API, so a drift here shows up as the
 * list and the paper disagreeing — which is the failure mode we want, not a silent one.
 *
 * Both sides treat a stored layout as a **partial statement**: keys it names are reordered and
 * toggled as it says, keys it has never heard of land at their catalog position with their
 * catalog default. That is what lets a template laid out today still print a field a later
 * release adds, instead of quietly omitting it.
 */

export interface FieldSpec {
  key: string;
  default: boolean;
  locked: boolean;
  /** Whether the field prints a label at all, and so whether it can be reworded. */
  labelled: boolean;
}

export interface BlockSpec {
  key: string;
  region: "identity" | "sender" | "addressee" | "meta" | "body";
  default: boolean;
  locked: boolean;
  movable: boolean;
  fields: FieldSpec[];
}

export interface TemplateLayoutField {
  key: string;
  enabled: boolean;
  /** The tenant's own wording for the label, per locale. Empty = the catalog's. */
  label_i18n?: Record<string, string>;
  locked?: boolean;
  /** Carried for the editor's own use; the API re-reads it from its catalog. */
  labelled?: boolean;
}

export interface TemplateLayoutBlock {
  key: string;
  enabled: boolean;
  fields: TemplateLayoutField[];
  /** Carried for the editor's own use; the API re-reads both from its catalog. */
  locked?: boolean;
  region?: BlockSpec["region"];
}

export interface TemplateBackground {
  enabled: boolean;
  file_id: string | null;
  use_logo: boolean;
  opacity: number;
  scale: number;
  x: number;
  y: number;
  rotate: number;
  repeat: boolean;
}

export interface TemplateConfig {
  design: "classic" | "letterhead" | "custom";
  accent_color: string | null;
  show_logo: boolean;
  columns: Record<"quantity" | "unit" | "unit_price" | "tax", boolean>;
  layout: TemplateLayoutBlock[];
  background: TemplateBackground;
  /** How the payment QR is drawn (epic #269). `brand` — the default — uses the accent colour
   *  and puts the tenant's logo in the middle; `plain` is black and white, for monochrome
   *  printing. A *style*, never a colour: the API replaces an accent too pale to scan, so
   *  there is no field here in which to type an unreadable invoice. */
  qr_style: "brand" | "plain";
  html: string | null;
  css: string | null;
  intro_i18n: Record<string, string>;
  payment_i18n: Record<string, string>;
  footer_i18n: Record<string, string>;
}

export const DEFAULT_BACKGROUND: TemplateBackground = {
  enabled: false,
  file_id: null,
  use_logo: true,
  opacity: 0.04,
  scale: 78,
  x: 50,
  y: 50,
  rotate: 0,
  repeat: false,
};

export const DEFAULT_CONFIG: TemplateConfig = {
  design: "classic",
  accent_color: null,
  show_logo: true,
  columns: { quantity: true, unit: false, unit_price: true, tax: true },
  layout: [],
  background: DEFAULT_BACKGROUND,
  qr_style: "brand",
  html: null,
  css: null,
  intro_i18n: {},
  payment_i18n: {},
  footer_i18n: {},
};

/** A stored config filled out with the defaults for everything it does not carry. */
export function toConfig(stored: unknown): TemplateConfig {
  const raw = (stored ?? {}) as Partial<TemplateConfig>;
  return {
    ...DEFAULT_CONFIG,
    ...raw,
    columns: { ...DEFAULT_CONFIG.columns, ...(raw.columns ?? {}) },
    background: { ...DEFAULT_BACKGROUND, ...(raw.background ?? {}) },
    intro_i18n: { nl: "", en: "", ...(raw.intro_i18n ?? {}) },
    payment_i18n: { nl: "", en: "", ...(raw.payment_i18n ?? {}) },
    footer_i18n: { nl: "", en: "", ...(raw.footer_i18n ?? {}) },
    layout: raw.layout ?? [],
  };
}

/** Move `from` to `to`, returning a new array. */
export function moveItem<T>(items: T[], from: number, to: number): T[] {
  if (from === to || from < 0 || to < 0 || from >= items.length || to >= items.length) {
    return items;
  }
  const next = items.slice();
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}

/**
 * Catalog keys ordered by the stored list, with the unmentioned kept in place.
 *
 * Placement walks the catalog and inserts each unseen key after the nearest preceding key
 * **already placed** — checking the growing result, not only the stored list, so a run of
 * consecutive new keys stays in catalog order rather than each landing at the front.
 */
function orderBy(storedKeys: string[], catalogKeys: string[]): string[] {
  const known = storedKeys.filter((key) => catalogKeys.includes(key));
  if (!known.length) return catalogKeys.slice();
  const merged = known.slice();
  for (const key of catalogKeys) {
    if (merged.includes(key)) continue;
    const before = catalogKeys.slice(0, catalogKeys.indexOf(key)).filter((k) => merged.includes(k));
    const at = before.length ? merged.indexOf(before[before.length - 1]) + 1 : 0;
    merged.splice(at, 0, key);
  }
  return merged;
}

/** The stored layout merged onto the catalog — what the editor lists and what it sends back. */
export function mergeLayout(
  layout: TemplateLayoutBlock[],
  catalog: BlockSpec[],
): TemplateLayoutBlock[] {
  const stored = new Map(layout.map((block) => [block.key, block]));
  const catalogKeys = catalog.map((block) => block.key);
  const byKey = new Map(catalog.map((block) => [block.key, block]));

  return orderBy(
    layout.map((block) => block.key),
    catalogKeys,
  ).map((key) => {
    const spec = byKey.get(key)!;
    const entry = stored.get(key);
    const storedFields = new Map((entry?.fields ?? []).map((field) => [field.key, field]));
    const fieldKeys = spec.fields.map((field) => field.key);
    return {
      key,
      region: spec.region,
      locked: spec.locked,
      enabled: spec.locked ? true : (entry?.enabled ?? spec.default),
      fields: orderBy(
        (entry?.fields ?? []).map((field) => field.key),
        fieldKeys,
      ).map((fieldKey) => {
        const fieldSpec = spec.fields.find((field) => field.key === fieldKey)!;
        const item = storedFields.get(fieldKey);
        return {
          key: fieldKey,
          locked: fieldSpec.locked,
          labelled: fieldSpec.labelled,
          enabled: fieldSpec.locked ? true : (item?.enabled ?? fieldSpec.default),
          // Dropped for a field that prints no label: an override there would not rename
          // anything, it would introduce one. Same rule the API resolves by.
          label_i18n: fieldSpec.labelled ? (item?.label_i18n ?? {}) : {},
        };
      }),
    };
  });
}

/** The layout stripped of the editor's own bookkeeping — what the API accepts. */
export function layoutForApi(layout: TemplateLayoutBlock[]) {
  return layout.map((block) => ({
    key: block.key,
    enabled: block.enabled,
    // An empty override is not one, and this is stored in a JSONB column every document read
    // touches: forty fields' worth of `{}` is forty pieces of nothing to carry around.
    fields: block.fields.map((field) =>
      Object.keys(field.label_i18n ?? {}).length
        ? { key: field.key, enabled: field.enabled, label_i18n: field.label_i18n }
        : { key: field.key, enabled: field.enabled },
    ),
  }));
}
