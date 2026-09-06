<script lang="ts">
  /**
   * ← 2026 → for a report whose period is a calendar year. Links, never a click handler: the
   * year is the view, so the back button and a pasted URL both land on the year that was open
   * (docs/UX.md, "the URL is the view"). `hrefFor` keeps every other query parameter the page
   * carries — a VAT toggle, a filter — because stepping a year must not reset them.
   */
  import { ChevronLeft, ChevronRight } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";

  let { year, hrefFor }: { year: number; hrefFor: (year: number) => string } = $props();

  const step =
    "inline-flex h-8 w-8 items-center justify-center rounded-lg border border-border text-text-muted hover:bg-surface hover:text-text";
</script>

<div class="flex items-center gap-1" data-sveltekit-preload-data="hover">
  <a href={hrefFor(year - 1)} class={step} aria-label={t("overview.year.previous")}>
    <ChevronLeft size={16} aria-hidden="true" />
  </a>
  <span class="min-w-12 text-center text-sm font-semibold tabular-nums text-text">{year}</span>
  <a href={hrefFor(year + 1)} class={step} aria-label={t("overview.year.next")}>
    <ChevronRight size={16} aria-hidden="true" />
  </a>
</div>
