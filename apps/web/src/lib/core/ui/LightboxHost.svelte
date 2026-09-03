<script lang="ts">
  /**
   * The app's one image viewer, mounted once in the app shell beside `ToastHost` and driven by
   * `lightbox.svelte.ts` (docs/UX.md, "a screenshot is shown"). A native `<dialog>` opened with
   * `showModal()`: it lives in the browser's top layer, so it draws above a `Modal` or a
   * `SlideOver` whatever their z-index — the e-mail detail modal is the commonest place an inline
   * picture is clicked — and focus containment and focus return come for free.
   *
   * What it does with the pixels: fit-to-screen on open, wheel / pinch / `+` `-` zoom around the
   * cursor, drag to pan once zoomed, double-click between fit and actual size, ← → through the set
   * the caller handed over, a swipe on touch, and the two exits somebody may still want — the
   * original in its own tab, and a download. Until the original lands it draws the thumbnail the
   * page already had, blurred, under a spinner, so a 4 MB screenshot opens instantly and sharpens
   * rather than opening black.
   *
   * Escape is answered **here and stops** (`stopPropagation`): `Modal` and `SlideOver` listen on
   * the window and would otherwise close the record underneath along with the picture.
   */
  import {
    ChevronLeft,
    ChevronRight,
    Download,
    ExternalLink,
    Maximize2,
    Minimize2,
    X,
    ZoomIn,
    ZoomOut,
  } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";
  import {
    closeLightbox,
    lightbox,
    showLightboxAt,
    stepLightbox,
  } from "$lib/core/ui/lightbox.svelte";
  import Spinner from "$lib/core/ui/Spinner.svelte";

  const MIN_SCALE = 1;
  const MAX_SCALE = 8;
  const WHEEL_STEP = 1.2;
  const KEY_STEP = 1.25;
  /** A horizontal drag longer than this at fit scale is a swipe to the neighbour. */
  const SWIPE_PX = 60;

  const current = $derived(lightbox());
  const image = $derived(current ? current.images[current.index] : null);
  const total = $derived(current?.images.length ?? 0);
  const hasPrev = $derived(!!current && current.index > 0);
  const hasNext = $derived(!!current && current.index < total - 1);

  let dialog = $state<HTMLDialogElement | null>(null);
  let stage = $state<HTMLDivElement | null>(null);
  let img = $state<HTMLImageElement | null>(null);
  let loaded = $state(false);
  let scale = $state(1);
  let tx = $state(0);
  let ty = $state(0);
  let dragging = $state(false);

  const zoomed = $derived(scale > MIN_SCALE);

  // Open and close follow the store, never the other way round: `onclose` (Escape, or the
  // browser closing it for us) writes the store, and this effect makes the element agree.
  $effect(() => {
    const el = dialog;
    if (!el) return;
    if (current && !el.open) el.showModal();
    else if (!current && el.open) el.close();
  });

  // The page behind must not scroll under a wheel meant for zooming.
  $effect(() => {
    if (!current) return;
    const previous = document.documentElement.style.overflow;
    document.documentElement.style.overflow = "hidden";
    return () => {
      document.documentElement.style.overflow = previous;
    };
  });

  // Every image starts at fit scale, and "loaded" is decided by a probe of the original — one
  // mechanism whether or not a thumbnail is drawn meanwhile. The visible <img> shows the thumb
  // until the probe lands, then flips to the (now cached) original in the same frame.
  $effect(() => {
    const target = image;
    loaded = false;
    scale = MIN_SCALE;
    tx = 0;
    ty = 0;
    if (!target) return;
    const probe = new Image();
    probe.onload = () => {
      loaded = true;
    };
    probe.src = target.src;
    return () => {
      probe.onload = null;
    };
  });

  // The neighbours are fetched while this one is being looked at, so ← → do not wait.
  $effect(() => {
    if (!current) return;
    for (const delta of [-1, 1]) {
      const neighbour = current.images[current.index + delta];
      if (neighbour) new Image().src = neighbour.src;
    }
  });

  // Wheel is attached by hand: a passive listener cannot `preventDefault`, and the page behind
  // a zoom gesture must not scroll.
  $effect(() => {
    const el = stage;
    if (!el) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const [cx, cy] = stagePoint(event.clientX, event.clientY);
      zoomBy(event.deltaY < 0 ? WHEEL_STEP : 1 / WHEEL_STEP, cx, cy);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  });

  function clamp(value: number, lo: number, hi: number): number {
    return Math.min(hi, Math.max(lo, value));
  }

  /** A client point as an offset from the stage's centre, which is the transform origin. */
  function stagePoint(clientX: number, clientY: number): [number, number] {
    if (!stage) return [0, 0];
    const rect = stage.getBoundingClientRect();
    return [clientX - (rect.left + rect.width / 2), clientY - (rect.top + rect.height / 2)];
  }

  /** Keep the picture over the stage: nothing may be panned entirely out of view. */
  function clampPan() {
    if (!stage || !img) return;
    const maxX = Math.max(0, (img.clientWidth * scale - stage.clientWidth) / 2);
    const maxY = Math.max(0, (img.clientHeight * scale - stage.clientHeight) / 2);
    tx = clamp(tx, -maxX, maxX);
    ty = clamp(ty, -maxY, maxY);
  }

  function setScale(next: number, cx = 0, cy = 0) {
    const target = clamp(next, MIN_SCALE, MAX_SCALE);
    if (target === scale) return;
    // The point under the cursor stays under the cursor: p' = p - (p - t) · (s'/s).
    const ratio = target / scale;
    tx = cx - (cx - tx) * ratio;
    ty = cy - (cy - ty) * ratio;
    scale = target;
    if (scale === MIN_SCALE) {
      tx = 0;
      ty = 0;
    }
    clampPan();
  }

  function zoomBy(factor: number, cx = 0, cy = 0) {
    setScale(scale * factor, cx, cy);
  }

  function fit() {
    setScale(MIN_SCALE);
  }

  /** One device pixel per image pixel — or, for a picture already smaller than the screen, a
   *  plain 2×, because "actual size" of a 300px icon is a no-op nobody meant. */
  function actualSize(cx = 0, cy = 0) {
    if (!img || img.naturalWidth === 0 || img.clientWidth === 0) return;
    const actual = img.naturalWidth / img.clientWidth;
    setScale(actual > 1.05 ? actual : 2, cx, cy);
  }

  function onDoubleClick(event: MouseEvent) {
    if (zoomed) fit();
    else actualSize(...stagePoint(event.clientX, event.clientY));
  }

  // Pointer handling: one pointer drags (a pan when zoomed, a swipe at fit scale), two pinch.
  // Pointer capture retargets every later event to the stage, so what was pressed is recorded
  // at the press — a release "on the stage" would otherwise also describe a click on the image.
  // A plain record, not a Map: nothing rendered reads it, and `svelte/prefer-svelte-reactivity`
  // rejects a mutated Map even where reactivity is not wanted.
  const pointers: Record<number, { x: number; y: number }> = {};
  const pointerCount = () => Object.keys(pointers).length;
  let pinch: { distance: number; scale: number } | null = null;
  let press: {
    x: number;
    y: number;
    tx: number;
    ty: number;
    moved: boolean;
    onImage: boolean;
  } | null = null;

  function distance(): number {
    const [a, b] = Object.values(pointers);
    return Math.hypot(a.x - b.x, a.y - b.y);
  }

  function onPointerDown(event: PointerEvent) {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    pointers[event.pointerId] = { x: event.clientX, y: event.clientY };
    stage?.setPointerCapture(event.pointerId);
    if (pointerCount() === 2) {
      pinch = { distance: distance(), scale };
      press = null;
    } else if (pointerCount() === 1) {
      press = {
        x: event.clientX,
        y: event.clientY,
        tx,
        ty,
        moved: false,
        onImage: event.target === img,
      };
    }
  }

  function onPointerMove(event: PointerEvent) {
    if (!(event.pointerId in pointers)) return;
    pointers[event.pointerId] = { x: event.clientX, y: event.clientY };
    if (pointerCount() === 2 && pinch) {
      setScale(pinch.scale * (distance() / pinch.distance));
      return;
    }
    if (!press) return;
    const dx = event.clientX - press.x;
    const dy = event.clientY - press.y;
    if (Math.abs(dx) > 4 || Math.abs(dy) > 4) press.moved = true;
    if (zoomed) {
      dragging = true;
      tx = press.tx + dx;
      ty = press.ty + dy;
      clampPan();
    }
  }

  function onPointerUp(event: PointerEvent) {
    delete pointers[event.pointerId];
    if (pointerCount() < 2) pinch = null;
    if (!press || pointerCount() > 0) return;
    const dx = event.clientX - press.x;
    const dy = event.clientY - press.y;
    if (!zoomed && Math.abs(dx) > SWIPE_PX && Math.abs(dx) > Math.abs(dy) * 1.5) {
      stepLightbox(dx < 0 ? 1 : -1);
    } else if (!press.moved && !press.onImage) {
      // The dark around the picture is the way out, as a modal's backdrop is.
      closeLightbox();
    }
    press = null;
    dragging = false;
  }

  function onPointerCancel(event: PointerEvent) {
    delete pointers[event.pointerId];
    if (pointerCount() < 2) pinch = null;
    if (pointerCount() === 0) {
      press = null;
      dragging = false;
    }
  }

  function onKeyDown(event: KeyboardEvent) {
    switch (event.key) {
      case "Escape":
        event.preventDefault();
        event.stopPropagation();
        closeLightbox();
        break;
      case "ArrowLeft":
        event.preventDefault();
        stepLightbox(-1);
        break;
      case "ArrowRight":
        event.preventDefault();
        stepLightbox(1);
        break;
      case "+":
      case "=":
        event.preventDefault();
        zoomBy(KEY_STEP);
        break;
      case "-":
        event.preventDefault();
        zoomBy(1 / KEY_STEP);
        break;
      case "0":
        event.preventDefault();
        fit();
        break;
    }
  }

  function fmtSize(bytes: number): string {
    if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    if (bytes >= 1024) return `${Math.round(bytes / 1024)} kB`;
    return `${bytes} B`;
  }

  // The ± pair is a keyboard-and-mouse convenience; a phone pinches and double-taps, and the
  // bar there has no room for two buttons the thumb never reaches for.
  const buttonClass =
    "inline-flex shrink-0 items-center justify-center rounded-full p-2 text-white/80 hover:bg-white/15 hover:text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-white/70 disabled:opacity-30 disabled:hover:bg-transparent";
  const zoomButtonClass = buttonClass.replace("inline-flex", "hidden sm:inline-flex");
</script>

<dialog
  bind:this={dialog}
  class="lightbox fixed inset-0 m-0 h-full w-full max-h-none max-w-none overflow-hidden bg-transparent p-0 text-white backdrop:bg-black/95 backdrop:backdrop-blur-sm"
  aria-label={t("lightbox.viewer")}
  onclose={closeLightbox}
  oncancel={(event) => {
    event.preventDefault();
    closeLightbox();
  }}
  onkeydown={onKeyDown}
>
  {#if current && image}
    <div class="flex h-full w-full flex-col">
      <div class="flex items-center gap-1 px-2 py-1.5 sm:px-3">
        <div class="min-w-0 flex-1 truncate px-1 text-sm" title={image.label ?? undefined}>
          {#if image.label}
            <span class="text-white">{image.label}</span>
          {/if}
          {#if image.sizeBytes != null}
            <span class="ml-2 text-xs text-white/60">{fmtSize(image.sizeBytes)}</span>
          {/if}
        </div>
        {#if total > 1}
          <span class="px-2 text-xs tabular-nums text-white/70" aria-live="polite">
            {t("lightbox.counter", { index: current.index + 1, total })}
          </span>
        {/if}
        <button
          type="button"
          class={zoomButtonClass}
          title={t("lightbox.zoom_out")}
          aria-label={t("lightbox.zoom_out")}
          disabled={!zoomed}
          onclick={() => zoomBy(1 / KEY_STEP)}
        >
          <ZoomOut size={18} />
        </button>
        <button
          type="button"
          class={zoomButtonClass}
          title={t("lightbox.zoom_in")}
          aria-label={t("lightbox.zoom_in")}
          disabled={scale >= MAX_SCALE}
          onclick={() => zoomBy(KEY_STEP)}
        >
          <ZoomIn size={18} />
        </button>
        <button
          type="button"
          class={buttonClass}
          title={zoomed ? t("lightbox.fit") : t("lightbox.actual_size")}
          aria-label={zoomed ? t("lightbox.fit") : t("lightbox.actual_size")}
          onclick={() => (zoomed ? fit() : actualSize())}
        >
          {#if zoomed}<Minimize2 size={18} />{:else}<Maximize2 size={18} />{/if}
        </button>
        <a
          href={image.src}
          target="_blank"
          rel="noopener noreferrer"
          class={buttonClass}
          title={t("files.open_original")}
          aria-label={t("files.open_original")}
        >
          <ExternalLink size={18} />
        </a>
        <a
          href={image.src}
          download={image.label || undefined}
          class={buttonClass}
          title={t("lightbox.download")}
          aria-label={t("lightbox.download")}
        >
          <Download size={18} />
        </a>
        <button
          type="button"
          class={buttonClass}
          title={t("common.close")}
          aria-label={t("common.close")}
          onclick={closeLightbox}
        >
          <X size={20} />
        </button>
      </div>

      <!-- The stage is a pointer surface over a picture, presentational by role: every act it
           offers (close, step, zoom) also has a button or a key. -->
      <div
        bind:this={stage}
        role="presentation"
        class="relative flex min-h-0 flex-1 touch-none items-center justify-center overflow-hidden select-none"
        style:cursor={zoomed ? (dragging ? "grabbing" : "grab") : "zoom-in"}
        onpointerdown={onPointerDown}
        onpointermove={onPointerMove}
        onpointerup={onPointerUp}
        onpointercancel={onPointerCancel}
        ondblclick={onDoubleClick}
      >
        <img
          bind:this={img}
          src={loaded || !image.thumb ? image.src : image.thumb}
          alt={image.label ?? ""}
          draggable="false"
          class="block max-h-full max-w-full object-contain will-change-transform {!loaded &&
          image.thumb
            ? 'blur-sm'
            : ''}"
          style:transform="translate({tx}px, {ty}px) scale({scale})"
          style:transition={dragging ? "none" : "transform 120ms ease-out, filter 200ms ease-out"}
          onload={() => {
            if (!image.thumb) loaded = true;
          }}
        />
        {#if !loaded}
          <div
            class="pointer-events-none absolute inset-0 flex items-center justify-center text-white/80"
            aria-label={t("common.loading")}
          >
            <Spinner size={28} />
          </div>
        {/if}
        {#if total > 1}
          <button
            type="button"
            class="{buttonClass} absolute top-1/2 left-2 -translate-y-1/2 bg-black/40 sm:left-4"
            title={t("common.previous")}
            aria-label={t("common.previous")}
            disabled={!hasPrev}
            onclick={() => stepLightbox(-1)}
          >
            <ChevronLeft size={24} />
          </button>
          <button
            type="button"
            class="{buttonClass} absolute top-1/2 right-2 -translate-y-1/2 bg-black/40 sm:right-4"
            title={t("common.next")}
            aria-label={t("common.next")}
            disabled={!hasNext}
            onclick={() => stepLightbox(1)}
          >
            <ChevronRight size={24} />
          </button>
        {/if}
      </div>

      {#if total > 1}
        <div class="flex justify-center gap-1.5 overflow-x-auto px-3 py-2">
          {#each current.images as item, i (item.src)}
            <button
              type="button"
              class="h-12 w-12 shrink-0 overflow-hidden rounded-md ring-2 transition-opacity {i ===
              current.index
                ? 'ring-white opacity-100'
                : 'ring-transparent opacity-50 hover:opacity-90'}"
              title={item.label ?? undefined}
              aria-label={t("lightbox.thumbnail_of", { index: i + 1 })}
              aria-current={i === current.index ? "true" : undefined}
              onclick={() => showLightboxAt(i)}
            >
              <img
                src={item.thumb ?? item.src}
                alt=""
                loading="lazy"
                class="h-full w-full object-cover"
              />
            </button>
          {/each}
        </div>
      {/if}
    </div>
  {/if}
</dialog>

<style>
  .lightbox[open] {
    animation: lightbox-in 140ms ease-out;
  }
  .lightbox[open]::backdrop {
    animation: lightbox-in 140ms ease-out;
  }
  @keyframes lightbox-in {
    from {
      opacity: 0;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .lightbox[open],
    .lightbox[open]::backdrop {
      animation: none;
    }
  }
</style>
