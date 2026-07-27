/**
 * Making a Gmail snippet readable (#263) — pure string work, no imports, so it is unit-tested
 * (`pnpm web test:unit`) the way `quoted.ts`'s sibling rules could be.
 */

// --- e-mail teasers (#263) ---------------------------------------------------------------- //
// Gmail hands us its `snippet` **HTML-escaped** and padded with the message's invisible
// preheader: `Beste Jan,&#39;s ochtends&nbsp;&zwnj;&zwnj;&zwnj;…`. Rendered raw that reads as
// mojibake and runs on for two hundred characters, so a list row shows a wall of escape codes
// instead of a teaser. Decode it, drop the invisible padding, collapse the whitespace — and
// cut it at a word boundary, never mid-word.
const NAMED_ENTITIES: Record<string, string> = {
  amp: "&",
  lt: "<",
  gt: ">",
  quot: '"',
  apos: "'",
  nbsp: " ",
  zwnj: "",
  shy: "",
  hellip: "…",
  mdash: "—",
  ndash: "–",
  laquo: "«",
  raquo: "»",
  euro: "€",
};

/** Zero-width joiners, BOMs and soft hyphens — a preheader's invisible padding. */
const INVISIBLE = /[\u00ad\u200b-\u200f\u2028\u2029\u202a-\u202e\u2060\ufeff]/g;

/** Decoded, de-padded, single-spaced — the readable text behind a raw Gmail snippet. */
export function cleanSnippet(raw: string | null | undefined): string {
  if (!raw) return "";
  return raw
    .replace(/&(#\d+|#[xX][0-9a-fA-F]+|[a-zA-Z]+);/g, (match, code: string) => {
      if (code.startsWith("#")) {
        const point = Number(
          code[1] === "x" || code[1] === "X" ? `0x${code.slice(2)}` : code.slice(1),
        );
        return Number.isFinite(point) && point > 0 && point <= 0x10ffff
          ? String.fromCodePoint(point)
          : match;
      }
      return NAMED_ENTITIES[code.toLowerCase()] ?? match;
    })
    .replace(INVISIBLE, "")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * A short teaser of an e-mail's body for a list row (#263) — cleaned, then cut on a word
 * boundary with an ellipsis. A CSS `truncate` alone can't do this: it fits the *column*, so a
 * wide screen still pours a full paragraph next to a one-word subject.
 */
export function snippetPreview(raw: string | null | undefined, max = 50): string {
  const text = cleanSnippet(raw);
  if (text.length <= max) return text;
  const cut = text.slice(0, max);
  const space = cut.lastIndexOf(" ");
  // Only honour the word boundary while it still leaves a teaser worth reading; one very long
  // word at the front would otherwise cut the preview down to nothing.
  return `${(space > max * 0.6 ? cut.slice(0, space) : cut).trimEnd()}…`;
}
