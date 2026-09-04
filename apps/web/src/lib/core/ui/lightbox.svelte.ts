/**
 * The app's one image viewer (docs/UX.md, "a screenshot is shown").
 *
 * Two surfaces draw a stored image inline — the attachment strip's thumbnails and a
 * `![alt](file:<uuid>)` marker in rendered markdown — and each had grown its own answer to a
 * click: the strip a bare `<dialog>` with the bytes in it, the markdown a `window.open` into a
 * new tab, which is a browser's default image page over a black background with no way back to
 * the record. Neither could step to the next screenshot, neither could zoom, and the two looked
 * nothing alike.
 *
 * So the viewer is one component (`LightboxHost`, mounted once in the app shell beside the toast
 * host) driven by this store, the shape `toast.svelte.ts` set: a module store, not a context,
 * because the click that opens it happens inside sanitized `{@html}` five components down from
 * anything that could hold a `<dialog>`. A caller hands over the **set** it is part of and the
 * index that was clicked, so ← / → walk the same screenshots the page shows, in the order it
 * shows them.
 */

export interface LightboxImage {
  /** The original bytes — the one thing the viewer exists to show. */
  src: string;
  /** A smaller rendering the page already loaded, drawn underneath until the original lands. */
  thumb?: string | null;
  /** What the caption says: a filename, or the marker's alt text. Empty says nothing. */
  label?: string | null;
  sizeBytes?: number | null;
}

interface LightboxState {
  images: LightboxImage[];
  index: number;
}

let current = $state<LightboxState | null>(null);

/** What is open right now — read by `LightboxHost` and by nothing else. */
export function lightbox(): LightboxState | null {
  return current;
}

/** Open the viewer on `images[index]`. A caller with nothing to show opens nothing. */
export function openLightbox(images: LightboxImage[], index = 0): void {
  if (images.length === 0) return;
  const at = Math.min(Math.max(index, 0), images.length - 1);
  current = { images: [...images], index: at };
}

export function closeLightbox(): void {
  current = null;
}

/** Step to a neighbour; the ends do not wrap, so the last arrow press is a no-op rather than a
 *  surprise jump back to the first screenshot. */
export function stepLightbox(delta: number): void {
  if (!current) return;
  const next = current.index + delta;
  if (next < 0 || next >= current.images.length) return;
  current = { ...current, index: next };
}

export function showLightboxAt(index: number): void {
  if (!current || index < 0 || index >= current.images.length) return;
  current = { ...current, index };
}
