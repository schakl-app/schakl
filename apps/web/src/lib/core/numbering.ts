/**
 * Number-format tokens — the browser-side mirror of `apps/api/app/core/numbering.py`.
 *
 * Exists only so a settings screen can show what a template *produces* while it is being
 * typed. The API remains the authority: it re-validates the format on save and is the only
 * thing that ever allocates a real number. Keep the two in step — a preview that disagrees
 * with what gets stored is worse than no preview.
 */

const SEQ = /\{seq(?::(\d{1,2}))?\}/g;
const KNOWN_TOKEN = /^\{(?:year|yy|seq(?::\d{1,2})?)\}$/;
const ANY_TOKEN = /\{[^{}]*\}/g;

/** The tokens to show in a hint, in the order they are worth learning. */
export const NUMBER_TOKENS = ["{year}", "{yy}", "{seq}", "{seq:4}"] as const;

/** A usable format: non-empty, exactly one `{seq}`, and no token the API won't understand. */
export function formatValid(fmt: string): boolean {
  if (!fmt || !fmt.trim()) return false;
  if ((fmt.match(SEQ) ?? []).length !== 1) return false;
  return (fmt.match(ANY_TOKEN) ?? []).every((token) => KNOWN_TOKEN.test(token));
}

/** Render one number from a format, e.g. `K{year}-{seq:4}` → `K2026-0007`. */
export function formatNumber(fmt: string, year: number, seq: number): string {
  return fmt
    .replace(SEQ, (_, pad: string | undefined) =>
      pad ? String(seq).padStart(Number(pad), "0") : String(seq),
    )
    .replace(/\{year\}/g, String(year))
    .replace(/\{yy\}/g, String(year % 100).padStart(2, "0"));
}
