<script lang="ts">
  /**
   * A stored PDF, in a frame — the original an imported invoice arrived with.
   *
   * `DocumentFrame` is for a page the API *rendered*: same-origin HTML it can measure and
   * scale, sandboxed without scripts because the document has none. A PDF is neither. The
   * browser draws it with its own viewer, which a `sandbox` attribute switches off (a plugin
   * is exactly what the flag exists to block), and whose height the page cannot read — so the
   * frame is unsandboxed on purpose and takes a viewport-sized height instead of measuring.
   * Both are safe here because the source is our own proxy on our own origin, serving bytes
   * the API already gated, under `frame-ancestors 'self'`.
   *
   * An `<object>` rather than an `<iframe>`, for the browser that has no viewer at all (a
   * headless one, some mobile ones): an iframe there starts a *download* on page load, while
   * an object falls through to its content — a sentence and the download link, which is the
   * same file offered on purpose instead of by accident.
   *
   * `version` rides in the URL for the same reason it does on `DocumentFrame`: a replaced
   * original has the same address as the one before it, and a frame does not refetch an
   * address it already has.
   */
  import { t } from "$lib/core/i18n";

  let {
    src,
    version = null,
    title,
    class: className = "",
  }: {
    /** The proxy that serves the bytes inline; `download` is the same route without it. */
    src: string;
    version?: string | number | null;
    title: string;
    class?: string;
  } = $props();

  const frameSrc = $derived(
    version == null
      ? src
      : `${src}${src.includes("?") ? "&" : "?"}v=${encodeURIComponent(String(version))}`,
  );
  /** The same bytes as an attachment: the inline flag is what the frame adds, so it is what the
   *  fallback link takes away. */
  const downloadHref = $derived(src.replace(/([?&])inline=1(&|$)/, "$1").replace(/[?&]$/, ""));
</script>

<div class="overflow-hidden {className}">
  <object
    data={frameSrc}
    type="application/pdf"
    {title}
    class="block h-[75vh] min-h-[32rem] w-full bg-white"
  >
    <div class="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
      <p class="text-sm text-text-muted">{t("common.pdf_no_viewer")}</p>
      <a
        href={downloadHref}
        class="inline-flex items-center rounded-lg border border-border px-3 py-2 text-sm font-medium text-text hover:bg-surface"
        >{t("common.download")}</a
      >
    </div>
  </object>
</div>
