<script lang="ts">
  /**
   * One number, its label, and — where the API sent one — the second figure that qualifies it
   * ("waarvan alle conversies"). A null value prints as a dash: cost per conversion with no
   * conversions is not zero, it is not computable.
   *
   * Drawn as a `stat` (docs/UX.md, the visual system): a tinted fill and no border, so a
   * figure reads as a figure against the white section it sits in. It used to be a hairline
   * box with no fill on the page's own ground — a tile the colour of the background.
   */
  import { t } from "$lib/core/i18n";

  import { fmtUnit, widgetTitle } from "./format";
  import type { LeadWidget } from "./types";

  let { widget }: { widget: LeadWidget } = $props();
</script>

<div class="flex h-full flex-col rounded-lg bg-surface-tint p-3">
  <p class="text-xs text-text-muted">{widgetTitle(widget)}</p>
  <p class="mt-0.5 text-2xl font-semibold tabular-nums text-text">
    {fmtUnit(widget.value, widget.unit, widget.currency)}
  </p>
  {#if widget.secondary !== null && widget.secondary !== undefined && widget.secondary_key}
    <p class="text-xs tabular-nums text-text-muted">
      {t(widget.secondary_key, { value: fmtUnit(widget.secondary, widget.unit, widget.currency) })}
    </p>
  {/if}
  {#if widget.note_key}
    <p class="mt-auto pt-2 text-[11px] leading-snug text-text-muted">{t(widget.note_key)}</p>
  {/if}
</div>
