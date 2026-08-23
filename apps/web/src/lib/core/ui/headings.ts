/**
 * The heading scale (#404) — four levels, stated once, so a container can never be quieter
 * than the thing it contains.
 *
 * That inversion was live on two screens, and it is worth naming because it does not look
 * like a bug in a diff. On the client hub the *band* headings (VASTGELEGD, NOG NIETS
 * VASTGELEGD) were 12 px uppercase muted while the panel headings inside them were 14 px
 * dark: the container was literally the quieter of the two. On a task, all seven section
 * headings were 12 px uppercase muted — the least legible treatment the system has, applied
 * to the page's own skeleton. Each one reads fine on its own; only the pair reads wrong, and
 * a pair is not something a per-screen fix ever looks at.
 *
 * So the rule is a ladder rather than a taste, and each rung is a *size* step:
 *
 * | level          | size | for                                                   |
 * |----------------|------|-------------------------------------------------------|
 * | `PAGE_TITLE`   | 20px | what this page is. One per page, in the title band.    |
 * | `BAND_HEADING` | 16px | a group of cards. Outranks every card inside it.       |
 * | `PANEL_HEADING`| 14px | one card's own title. `Card` draws this for you.       |
 * | `FIELD_LABEL`  | 12px | one field's name inside a card. The quietest rung.     |
 *
 * **Uppercase is not a rung.** It was doing duty as one — a 12 px uppercase muted string was
 * the app's way of saying "this is structural" — and it cannot, because uppercase makes text
 * *harder* to scan, not more important. Where structure needs saying, say it a size louder.
 * The one place uppercase survives is a `FIELD_LABEL` over a figure (`SummaryStrip`), where
 * the label is genuinely subordinate to the number under it and wants to recede.
 *
 * These are plain strings rather than a component because Tailwind's JIT scans source for
 * literal class names, and because half the consumers are `<h2 class={...}>` inside a snippet
 * that already exists. `Card` and `PageHeader` cover the cases where a component is better.
 */

/** The page's own title. One per page — the title band draws it (`PageHeader`). */
export const PAGE_TITLE = "text-xl font-semibold text-text";

/**
 * A band: a named group of cards. 16 px and dark, so it outranks the 14 px panel headings
 * beneath it. Never muted — a heading that structures a page is not a footnote about it.
 */
export const BAND_HEADING = "text-base font-semibold text-text";

/** One card's title. `Card` renders this; reach for the constant only outside a `Card`. */
export const PANEL_HEADING = "text-sm font-semibold text-text";

/** One field's name. The quietest rung, and the only one that may be muted. */
export const FIELD_LABEL = "text-xs font-medium text-text-muted";
