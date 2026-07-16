/** Server-side helpers shared by the invoice/quote editor routes (issue #207). */

/** Map the editor's FormData onto the API's document body. Lines ride as one JSON field. */
export function documentBody(form: FormData): Record<string, unknown> {
  const text = (key: string) => {
    const value = String(form.get(key) ?? "").trim();
    return value || undefined;
  };
  let lines: unknown[];
  try {
    lines = JSON.parse(String(form.get("lines") ?? "[]"));
  } catch {
    lines = [];
  }
  return {
    contact_id: text("contact_id") || null,
    currency: text("currency"),
    exchange_rate: text("exchange_rate"),
    locale: text("locale"),
    reference: text("reference") ?? null,
    intro: text("intro") ?? null,
    notes: text("notes") ?? null,
    template_id: text("template_id") || null,
    issue_date: text("issue_date") ?? null,
    prices_include_tax: form.get("prices_include_tax") === "1",
    lines,
  };
}

/** The subset an issued document may still change (mirrors the API's post-issue set). */
export function processBody(form: FormData): Record<string, unknown> {
  const text = (key: string) => {
    const value = String(form.get(key) ?? "").trim();
    return value || undefined;
  };
  return {
    contact_id: text("contact_id") || null,
    locale: text("locale"),
    reference: text("reference") ?? null,
    intro: text("intro") ?? null,
    notes: text("notes") ?? null,
    template_id: text("template_id") || null,
    exchange_rate: text("exchange_rate"),
  };
}

export interface ContactLookup {
  id: string;
  name: string;
  company_ids: string[];
}

export function contactLookups(items: unknown[] | undefined): ContactLookup[] {
  return ((items ?? []) as Record<string, unknown>[]).map((c) => ({
    id: String(c.id),
    name: [c.first_name, c.last_name].filter(Boolean).join(" "),
    company_ids: ((c.companies ?? []) as { company_id: string }[]).map((link) => link.company_id),
  }));
}
