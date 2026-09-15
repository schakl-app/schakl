<script lang="ts">
  /**
   * Pick a named calendar month or quarter to report on (#316).
   *
   * A menu of **links**, not a select with a change handler: the period is part of the view, so
   * it lives in the URL (§9) — the back button returns to the month you were looking at, and the
   * link you paste into a chat shows your colleague the same numbers.
   *
   * Its options come from `anchor`, today on the **tenant's** calendar (`anchorMonth()`), so the
   * newest month offered is never one that has not begun where the tenant lives. Deliberately not
   * the streamed payload's `current_end`: that would put the control behind the very thing the
   * page streams, and a picker that appears a second after the page did reads as a glitch.
   */
  import { Calendar, Check } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";

  import { monthOptions, quarterOptions, type PeriodOption } from "./periods";

  let {
    anchor,
    active,
    label,
    urlFor,
  }: {
    /** An ISO date inside the newest period worth offering (the API's `current_end`). */
    anchor: string;
    /** The active period token, so the chosen option can be ticked. */
    active: string;
    /** What the trigger reads when a named period is active — the period's own name. */
    label: string;
    urlFor: (period: string) => string;
  } = $props();

  let open = $state(false);
  let root: HTMLElement | undefined = $state();
  // Fixed coordinates measured off the trigger, the ActionsMenu pattern: the tab row sits inside
  // scrollable containers on mobile, and an absolutely-positioned panel is clipped by them.
  let panelStyle = $state("");

  const months = $derived(monthOptions(anchor));
  const quarters = $derived(quarterOptions(anchor));
  // A free span (`2026-08-29..2026-09-03`, docs/MARKETING.md) is the third kind of period: the
  // one a measurement breakpoint needs, which is neither a month nor a trailing window.
  const RANGE_RE = /^(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})$/;
  const rangeMatch = $derived(RANGE_RE.exec(active));
  const isNamed = $derived(
    Boolean(rangeMatch) || [...months, ...quarters].some((o: PeriodOption) => o.token === active),
  );
  let from = $state("");
  let to = $state("");
  $effect(() => {
    from = rangeMatch?.[1] ?? "";
    to = rangeMatch?.[2] ?? anchor;
  });
  const rangeHref = $derived(from && to && from <= to ? urlFor(`${from}..${to}`) : "");

  function toggle() {
    if (!open && root) {
      const rect = root.getBoundingClientRect();
      const spaceBelow = window.innerHeight - rect.bottom;
      const estimated = 340;
      const vertical =
        spaceBelow < estimated && rect.top > spaceBelow
          ? `bottom: ${window.innerHeight - rect.top + 4}px;`
          : `top: ${rect.bottom + 4}px;`;
      panelStyle =
        vertical + `left: ${Math.max(8, Math.min(rect.left, window.innerWidth - 280))}px;`;
    }
    open = !open;
  }
</script>

<svelte:window
  onclick={(e) => {
    if (open && root && !root.contains(e.target as Node)) open = false;
  }}
  onkeydown={(e) => {
    if (e.key === "Escape") open = false;
  }}
  onscrollcapture={() => {
    if (open) open = false;
  }}
/>

<div class="relative shrink-0" bind:this={root}>
  <button
    type="button"
    class="flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-sm {isNamed
      ? 'border-brand text-brand'
      : 'border-border text-text hover:border-brand'}"
    onclick={toggle}
    aria-haspopup="menu"
    aria-expanded={open}
  >
    <Calendar size={14} />
    {isNamed ? label : t("marketing.period.pick")}
  </button>

  {#if open}
    <div
      role="menu"
      style={panelStyle}
      class="fixed z-30 max-h-96 w-72 overflow-y-auto rounded-xl border border-border bg-surface-raised py-1 shadow-lg"
      data-sveltekit-preload-data="hover"
    >
      <!-- A free span, first: two dates and a link. The link is the control (the URL is the
           view), so a span the reader typed is a bookmark the moment they open it. -->
      <p class="px-4 pt-2 pb-1 text-xs font-medium text-text-muted">
        {t("marketing.period.custom")}
      </p>
      <div class="flex flex-wrap items-center gap-1.5 px-4 pb-2">
        <input
          type="date"
          bind:value={from}
          max={anchor}
          aria-label={t("marketing.period.from")}
          class="w-[7.6rem] rounded border border-border bg-surface px-1.5 py-1 text-xs text-text outline-none focus:border-brand"
        />
        <span class="text-xs text-text-muted">–</span>
        <input
          type="date"
          bind:value={to}
          max={anchor}
          aria-label={t("marketing.period.to")}
          class="w-[7.6rem] rounded border border-border bg-surface px-1.5 py-1 text-xs text-text outline-none focus:border-brand"
        />
        {#if rangeHref}
          <a
            role="menuitem"
            href={rangeHref}
            onclick={() => (open = false)}
            data-sveltekit-noscroll
            class="rounded bg-brand px-2 py-1 text-xs font-medium text-white"
          >
            {t("common.apply")}
          </a>
        {:else}
          <span class="rounded bg-surface px-2 py-1 text-xs text-text-muted"
            >{t("common.apply")}</span
          >
        {/if}
      </div>
      <p class="border-t border-border px-4 pt-2 pb-1 text-xs font-medium text-text-muted">
        {t("marketing.period.months")}
      </p>
      {#each months as option (option.token)}
        <a
          role="menuitem"
          href={urlFor(option.token)}
          onclick={() => (open = false)}
          data-sveltekit-noscroll
          class="flex items-center justify-between gap-2 px-4 py-2 text-sm hover:bg-surface {option.token ===
          active
            ? 'text-brand'
            : 'text-text'}"
        >
          {option.label}
          {#if option.token === active}<Check size={14} />{/if}
        </a>
      {/each}
      <p class="border-t border-border px-4 pt-2 pb-1 text-xs font-medium text-text-muted">
        {t("marketing.period.quarters")}
      </p>
      {#each quarters as option (option.token)}
        <a
          role="menuitem"
          href={urlFor(option.token)}
          onclick={() => (open = false)}
          data-sveltekit-noscroll
          class="flex items-center justify-between gap-2 px-4 py-2 text-sm hover:bg-surface {option.token ===
          active
            ? 'text-brand'
            : 'text-text'}"
        >
          {option.label}
          {#if option.token === active}<Check size={14} />{/if}
        </a>
      {/each}
    </div>
  {/if}
</div>
