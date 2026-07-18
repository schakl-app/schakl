/** Shared parsing for the sidebar-layout editor's posted payload (#169). */

/** A tenant's per-locale label (locale → text); matches the API's `{[locale]: string}` shape. */
export type NavLabel = Record<string, string>;

/** Parse the editor's JSON `items` field into order + visibility; junk becomes an empty list. */
export function parseNavItems(raw: FormDataEntryValue | null): { key: string; hidden: boolean }[] {
  try {
    const parsed = JSON.parse(String(raw ?? "[]"));
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((item) => item && typeof item.key === "string" && item.key)
      .map((item) => ({ key: String(item.key), hidden: Boolean(item.hidden) }));
  } catch {
    return [];
  }
}

/** Collect a tenant label from the I18nTextField inputs `<base>_nl` / `<base>_en`; empties drop. */
function collectLabel(form: FormData, base: string): NavLabel | undefined {
  const label: NavLabel = {};
  for (const locale of ["nl", "en"] as const) {
    const text = String(form.get(`${base}_${locale}`) ?? "").trim();
    if (text) label[locale] = text;
  }
  return Object.keys(label).length ? label : undefined;
}

/**
 * The org-default editor's full payload (#169): order + visibility from the `items` JSON, plus
 * the tenant labels each item's and group's I18nTextField posted (`itemlabel_<key>_<locale>`,
 * `grouplabel_<key>_<locale>`). Renaming is org config, so this richer shape is only built for
 * the default save — the personal editor still posts order/visibility alone (`parseNavItems`),
 * and the API strips any labels a personal PUT carries anyway.
 */
export function buildNavDefaultPayload(form: FormData): {
  items: { key: string; hidden: boolean; label?: NavLabel }[];
  groups: { key: string; label?: NavLabel }[];
} {
  const items = parseNavItems(form.get("items")).map((item) => ({
    ...item,
    label: collectLabel(form, `itemlabel_${item.key}`),
  }));
  let groupKeys: string[] = [];
  try {
    const parsed = JSON.parse(String(form.get("groups") ?? "[]"));
    if (Array.isArray(parsed)) {
      groupKeys = parsed
        .map((g) => (typeof g === "string" ? g : String(g?.key ?? "")))
        .filter(Boolean);
    }
  } catch {
    groupKeys = [];
  }
  const groups = groupKeys.map((key) => ({ key, label: collectLabel(form, `grouplabel_${key}`) }));
  return { items, groups };
}
