/**
 * Note variables for subscriptions and their standard-subscription presets (issue #259).
 *
 * A standard subscription's notes are a transparency field: what the agency will do for the
 * client and what the client may expect. It is authored once with `{{company_name}}`-style
 * placeholders and **the placeholders stay in storage** — a subscription and its preset both
 * keep the raw `{{…}}` forever. They are resolved only at the edges:
 *
 *   - **editing** shows a live *preview* of the resolved text, with a not-yet-known value drawn
 *     as a bracketed `[label]` placeholder rather than left as a raw token;
 *   - **displaying** a subscription's note resolves the tokens against that agreement's own
 *     details, so a reader never sees that a variable was ever there;
 *   - a **preset** is the definition — it never resolves in place (it has no company to resolve
 *     against), so its list shows the tokens exactly as authored.
 *
 * Resolution is a plain string substitution over the markdown *source* (the value the shared
 * `RichTextEditor` stores). Tokens are language-neutral, stable slugs; the human labels live in
 * i18n (`subscriptions.variables.*`) and are only ever shown, never stored.
 */
import { fmtMoney, fmtNumber, fmtNumericDate } from "$lib/core/format";
import { t } from "$lib/core/i18n";

/** The variables a note may draw on, in the order the insert menu offers them. */
export const SUBSCRIPTION_NOTE_VARIABLES = [
  "company_name",
  "subscription_name",
  "type",
  "amount",
  "interval",
  "included_hours",
  "start_date",
  "brand_name",
] as const;

export type NoteVariableKey = (typeof SUBSCRIPTION_NOTE_VARIABLES)[number];

const KEY_SET: ReadonlySet<string> = new Set(SUBSCRIPTION_NOTE_VARIABLES);
// A non-global instance for `.test()` (no `lastIndex` state); `.replace()` uses its own literal.
const TOKEN_RE = /\{\{\s*[a-zA-Z_]+\s*\}\}/;

/** True when the source holds at least one `{{token}}` — the gate for showing a preview. */
export function hasNoteVariables(source: string | null | undefined): boolean {
  return !!source && TOKEN_RE.test(source);
}

/** The `{token, label}` items the editor's insert menu renders. */
export function noteVariableItems(
  translate: (key: string) => string,
): { token: string; label: string }[] {
  return SUBSCRIPTION_NOTE_VARIABLES.map((key) => ({
    token: `{{${key}}}`,
    label: translate(`subscriptions.variables.${key}`),
  }));
}

/** For previews: an unresolved token renders as its bracketed human label, e.g. `[Company name]`. */
export function notePlaceholder(
  translate: (key: string) => string,
): (key: NoteVariableKey) => string {
  return (key) => `[${translate(`subscriptions.variables.${key}`)}]`;
}

/**
 * Replace every known `{{key}}` in `source`. A known token with a value takes the value; a known
 * token *without* one takes `opts.placeholder(key)` if given (preview) or the empty string (a
 * display must never leak a raw variable). An **unknown** token (a typo, some other syntax) is
 * left verbatim so its author can see and fix it.
 */
export function resolveNoteVariables(
  source: string,
  values: Partial<Record<NoteVariableKey, string | null | undefined>>,
  opts?: { placeholder?: (key: NoteVariableKey) => string },
): string {
  if (!source) return source;
  return source.replace(/\{\{\s*([a-zA-Z_]+)\s*\}\}/g, (match, raw: string) => {
    if (!KEY_SET.has(raw)) return match;
    const key = raw as NoteVariableKey;
    const value = values[key];
    if (value != null && value !== "") return value;
    return opts?.placeholder ? opts.placeholder(key) : "";
  });
}

/** Build the resolved values from an agreement's fields (a saved subscription or a live form). */
export function subscriptionNoteValues(input: {
  companyName?: string | null;
  subscriptionName?: string | null;
  typeLabel?: string | null;
  amount?: number | string | null;
  interval?: string | null;
  includedHours?: number | string | null;
  startDate?: string | null;
  brandName?: string | null;
}): Partial<Record<NoteVariableKey, string>> {
  const num = (value: number | string | null | undefined): number | null => {
    if (value == null || value === "") return null;
    const n = Number(value);
    return Number.isNaN(n) ? null : n;
  };
  const amount = num(input.amount);
  const hours = num(input.includedHours);
  return {
    company_name: input.companyName || undefined,
    subscription_name: input.subscriptionName || undefined,
    type: input.typeLabel || undefined,
    amount: amount != null ? fmtMoney(amount) : undefined,
    interval: input.interval ? t(`subscriptions.interval.${input.interval}`) : undefined,
    included_hours: hours != null ? fmtNumber(hours) : undefined,
    start_date: input.startDate ? fmtNumericDate(input.startDate) : undefined,
    brand_name: input.brandName || undefined,
  };
}
