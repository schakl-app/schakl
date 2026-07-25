/**
 * Note variables for subscriptions and their standard-subscription presets (issue #259).
 *
 * A standard subscription's notes are a transparency field: what the agency will do for the
 * client and what the client may expect. Authored once with `{{company_name}}`-style
 * placeholders, they are **filled in with the concrete agreement's details when a real
 * subscription is created or saved** (a snapshot into the stored markdown, not a live binding)
 * — so the note reads as the plain agreement it is everywhere it is shown, and a later rename
 * or price change never silently rewrites what was agreed.
 *
 * Resolution is a plain string substitution over the markdown *source* (the value the shared
 * `RichTextEditor` stores). Tokens are language-neutral, stable slugs; the human labels live in
 * i18n (`subscriptions.variables.*`) and are only ever shown, never stored. Only *known* tokens
 * with a non-empty value resolve — an unknown or not-yet-filled `{{…}}` is left untouched rather
 * than baked to an empty string.
 */

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

/** The `{token, label}` items the editor's insert menu renders. */
export function noteVariableItems(
  translate: (key: string) => string,
): { token: string; label: string }[] {
  return SUBSCRIPTION_NOTE_VARIABLES.map((key) => ({
    token: `{{${key}}}`,
    label: translate(`subscriptions.variables.${key}`),
  }));
}

/**
 * Replace every `{{key}}` in `source` for which `values[key]` is a non-empty string. Whitespace
 * inside the braces is tolerated (`{{ company_name }}`); an unknown key, or one whose value is
 * missing/blank, is left verbatim so a half-filled note never bakes an empty value.
 */
export function resolveNoteVariables(
  source: string,
  values: Partial<Record<NoteVariableKey, string | null | undefined>>,
): string {
  if (!source) return source;
  return source.replace(/\{\{\s*([a-zA-Z_]+)\s*\}\}/g, (match, key: string) => {
    const value = values[key as NoteVariableKey];
    return value != null && value !== "" ? value : match;
  });
}
