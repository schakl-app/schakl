<script lang="ts">
  /**
   * The rendered document, in a frame.
   *
   * This replaces `DocumentView.svelte`, which redrew in Svelte what `pdf.py` drew in fpdf —
   * two implementations of one document, each carrying a comment telling the next person to
   * change both. The API now renders the document *once*, as HTML, and prints that same HTML
   * to PDF; so the preview's job is to show that page, not to reproduce it.
   *
   * Two things follow from it being a frame rather than markup:
   *
   * - A tenant's own HTML/CSS template can be previewed at all. A Svelte component cannot
   *   render someone else's Jinja, and a design that only appears once it is downloaded is a
   *   design nobody can edit.
   * - The document's stylesheet is its own. An invoice is paper — fixed A4, white, ink that
   *   ignores the app theme — and inside a frame it cannot inherit the app's cascade or leak
   *   its own into the page around it.
   *
   * The frame is same-origin (so it can be measured and printed) and `sandbox`ed without
   * `allow-scripts`: the document has no scripts of its own, and the API serves it under a
   * CSP that says so.
   */
  import { t } from "$lib/core/i18n";

  let {
    src = null,
    version = null,
    srcdoc = null,
    title,
    loading = false,
    class: className = "",
  }: {
    /** A URL serving the document HTML — the detail and print surfaces. */
    src?: string | null;
    /**
     * Whatever changes when the document changes — `updated_at` on the record it draws.
     *
     * A frame does not refetch a URL it already has. `/invoices/{id}/preview` is the same
     * string before and after the invoice is issued, so the page reloaded its data, the
     * status pill flipped to *Open*, and the document beside it went on saying CONCEPT. The
     * proxy already sends `no-store`; the request was never made. So the version travels in
     * the URL, and every write moves it.
     */
    version?: string | number | null;
    /** The HTML itself — the settings editor, whose config is not saved yet. */
    srcdoc?: string | null;
    title: string;
    loading?: boolean;
    class?: string;
  } = $props();

  const frameSrc = $derived.by(() => {
    if (!src) return null;
    if (version == null) return src;
    return `${src}${src.includes("?") ? "&" : "?"}v=${encodeURIComponent(String(version))}`;
  });

  let frame = $state<HTMLIFrameElement | null>(null);
  let host = $state<HTMLDivElement | null>(null);
  /** Natural height of the document, in px, once it has laid out. */
  let contentHeight = $state(0);
  let hostWidth = $state(0);

  /** A4 at the 96dpi the document's own screen styles assume. */
  const PAGE_WIDTH = 794;
  /** One page, for the moment between mounting and the first measurement. */
  const A4_HEIGHT = 1123;
  /**
   * Shrink to fit a narrow column rather than scrolling sideways (docs/UX.md is mobile-first,
   * and the page body must never scroll horizontally). Never scale *up*: a document blown
   * past A4 stops being a preview of the paper.
   */
  const scale = $derived(hostWidth > 0 ? Math.min(1, hostWidth / PAGE_WIDTH) : 1);

  function measure() {
    const doc = frame?.contentDocument;
    if (!doc?.documentElement) return;
    contentHeight = doc.documentElement.scrollHeight;
  }

  $effect(() => {
    // Re-measure when the source changes: the editor swaps `srcdoc` on every edit, and a
    // taller document behind a frame frozen at the old height is a silently cropped preview.
    void frameSrc;
    void srcdoc;
    const timer = setTimeout(measure, 50);
    return () => clearTimeout(timer);
  });

  $effect(() => {
    if (!host) return;
    const observer = new ResizeObserver((entries) => {
      hostWidth = entries[0]?.contentRect.width ?? 0;
    });
    observer.observe(host);
    return () => observer.disconnect();
  });

  /** Print just the document — not the app shell around it. */
  export function print() {
    frame?.contentWindow?.focus();
    frame?.contentWindow?.print();
  }
</script>

<!-- The frame is drawn at full A4 width and *scaled*, and a transform does not change layout —
     so the frame is taken out of flow and the host is given the height the scaled box really
     occupies. Sizing the host instead of the frame is what keeps the document's own CSS
     working in the pixels it was written for. -->
<div
  bind:this={host}
  class="relative w-full overflow-hidden {className}"
  style="height: {(contentHeight || A4_HEIGHT) * scale}px;"
>
  {#if loading}
    <div
      class="absolute inset-0 z-10 flex items-center justify-center bg-surface/70 text-sm text-text-muted"
      role="status"
    >
      {t("common.loading")}
    </div>
  {/if}
  <iframe
    bind:this={frame}
    src={frameSrc}
    {srcdoc}
    {title}
    onload={measure}
    sandbox="allow-same-origin allow-modals"
    class="absolute left-0 top-0 border-0 bg-white"
    style="width: {PAGE_WIDTH}px; height: {contentHeight || A4_HEIGHT}px; transform: scale({scale});
           transform-origin: top left;"
  ></iframe>
</div>
