/**
 * The inline-image marker, shared by the renderer and the editor (the inline-images task).
 *
 * `![alt](file:<uuid>)` is the one image form the platform draws (core/markdown.ts): it can
 * only ever name a file this instance stored and serves, never a remote host. Introduced for
 * a received e-mail's `cid:` parts, it now also carries an image pasted into a task's
 * description or a comment — same marker, same renderer, same guarantee.
 *
 * The optional ` =NN%` suffix is the author's width, as a percentage of the text column —
 * `![shot](file:…-… =50%)`. Absent means natural size, capped at the column width, so a
 * pasted screenshot never overflows and a small icon is never blown up. This module is pure
 * (no DOM, no DOMPurify) so the grammar has a unit test the browser modules can share.
 */

const UUID_SOURCE =
  "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";

/** The marker grammar: alt, file id, optional width percent. Unanchored — callers anchor. */
export const FILE_IMAGE_SOURCE = `!\\[([^\\]]*)\\]\\(file:(${UUID_SOURCE})(?: =(\\d{1,3})%)?\\)`;

/** The width presets the editor offers. "Auto" (natural size) is the absent case. */
export const IMAGE_WIDTHS = [25, 50, 75, 100] as const;

/**
 * A width somebody stored, clamped to what the renderer will draw: an integer percentage
 * between 10 and 100, or `null` for anything else (absent, garbage, a hand-edited `250`).
 * The floor exists because a 1%-wide image is a control that looks broken, not a choice.
 */
export function clampImageWidth(raw: unknown): number | null {
  const value = typeof raw === "string" ? Number.parseInt(raw, 10) : raw;
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  const width = Math.round(value);
  return width >= 10 && width <= 100 ? width : null;
}

/** Alt text that cannot break out of the marker: the bracket/paren characters go. */
export function cleanImageAlt(alt: string): string {
  return alt.replace(/[[\]()\n\r]/g, " ").replace(/\s+/g, " ").trim();
}

/** Build the marker the serializer stores. A full-width choice is stored explicitly — the
 *  author picked it, and "100%" and "natural size" are different renders for a small image. */
export function fileImageMarkdown(id: string, alt: string, width: unknown): string {
  const clamped = clampImageWidth(width);
  const suffix = clamped === null ? "" : ` =${clamped}%`;
  return `![${cleanImageAlt(alt)}](file:${id}${suffix})`;
}
