<script lang="ts">
  /**
   * The pager every list ends with (docs/PERFORMANCE.md, docs/UX.md). One component, so a list
   * that grows past a screenful gains real paging rather than a "showing the first 200" apology.
   *
   * **The controls are links, not buttons.** `<a href>` is what gives the back button, middle
   * click, preload-on-hover and a shareable page for free; a `goto()` handler gives none of them
   * and is the reason "I opened a client from page 4 and came back to page 1" was ever possible.
   *
   * **The size selector navigates *and* remembers.** The URL is the current view and the
   * preference is only the default (`paging.ts`), so picking 100 writes `?size=100` for this
   * view and hands `onsize` the number to persist for the next visit. Changing it returns to
   * page 1: page 4 of 50-row pages is not page 4 of 100-row pages, and silently keeping the
   * number would scroll the user somewhere they did not ask to be.
   *
   * **A phone gets the same information, not the same widget.** Below `sm` the numbered pages
   * collapse to "Page 2 of 17" — twelve tap targets 6 px apart is not a control.
   *
   * **The bar is unconditional; only the stepping stands down** (#334). The count and the size
   * selector are information, not decoration, so they render at twelve rows and at zero — see
   * `hasPageSteps`. Which leaves one trap worth naming, because this component can no longer
   * decline to print: **do not hand it a count you did not compute.** A list read with
   * `count=false` gets `total = len(items)` back by contract, and an always-on pager would then
   * say "1–50 of 50" over four thousand rows with total confidence. `count=false` belongs on
   * pickers and lookups — never on the read behind a `<Pagination>`.
   */
  import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from "@lucide/svelte";

  import { goto } from "$app/navigation";
  import { page as pageState } from "$app/state";
  import { t } from "$lib/core/i18n";
  import {
    hasPageSteps,
    PAGE_SIZES,
    pageCount,
    pageHref,
    pageWindow,
  } from "$lib/core/table/paging";

  let {
    total,
    page,
    limit,
    onsize,
    sizes = PAGE_SIZES as readonly number[],
  }: {
    /**
     * The API's count of the whole set — never `rows.length`, which counts this page (#37), and
     * never the total of a `count=false` read, which *is* `len(items)` (#334).
     */
    total: number;
    /** 1-based, as the load resolved it. */
    page: number;
    /** Rows per page, as the load resolved it. */
    limit: number;
    /** Persist the chosen size as this list's default. Omit and the choice lasts one view. */
    onsize?: (size: number) => void;
    /** Override the offered sizes where a row is unusually expensive to draw. */
    sizes?: readonly number[];
  } = $props();

  const pages = $derived(pageCount(total, limit));
  const from = $derived(total === 0 ? 0 : (page - 1) * limit + 1);
  const to = $derived(Math.min(page * limit, total));
  const steps = $derived(pageWindow(page, pages));

  /** Arrows and numbered chips only; the frame around them always renders (`hasPageSteps`). */
  const steppable = $derived(hasPageSteps(page, pages));

  function href(target: number): string {
    return pageHref(pageState.url, target);
  }

  function chooseSize(event: Event) {
    const size = Number((event.currentTarget as HTMLSelectElement).value);
    if (!Number.isFinite(size) || size < 1) return;
    const url = new URL(pageState.url);
    url.searchParams.set("size", String(size));
    url.searchParams.delete("page"); // a new page size starts at the first page
    onsize?.(size);
    void goto(url, { keepFocus: true, noScroll: true });
  }

  /**
   * The shape of a control, with **no display utility of its own** — `desktop` adds one, and two
   * competing ones (`inline-flex` here, `hidden` at the call site) do not resolve by the order
   * they appear in the attribute. Tailwind emits both and the later *rule* wins, which is how a
   * phone ended up showing the numbered pages it was supposed to collapse.
   */
  const boxClass = "h-8 min-w-8 items-center justify-center px-2";
  const linkClass = `${boxClass} rounded-lg border border-border text-text hover:border-brand hover:text-brand`;
  const mutedClass = `${boxClass} text-text-muted`;
  /** Below `sm` the numbered pages give way to "Pagina 2 van 17" — twelve 6 px targets is not a control. */
  const desktop = "hidden sm:inline-flex";
</script>

<nav
  class="mt-4 flex flex-col gap-3 text-sm sm:flex-row sm:items-center sm:justify-between"
  aria-label={t("table.paging.label")}
  data-sveltekit-preload-data="hover"
>
  <!-- At zero the range computes to "0–0 van 0", which is arithmetic rather than an answer. The
       size selector and the frame stay: a filter that matched nothing is still a view of a list. -->
  <span class="text-text-muted">
    {total === 0 ? t("table.paging.empty") : t("table.paging.range", { from, to, total })}
  </span>

  <div class="flex items-center justify-between gap-3 sm:justify-end">
    <label class="flex items-center gap-2 text-text-muted">
      <!-- "Per pagina" is dropped on a phone to make room for the arrows beside it. With the
           arrows stood down there is room, and a lone "50" combo box with nothing next to it is
           the one place the label is genuinely needed. -->
      <span class={steppable ? "hidden sm:inline" : "inline"}>{t("table.paging.size")}</span>
      <select
        value={limit}
        onchange={chooseSize}
        data-testid="page-size"
        aria-label={t("table.paging.size")}
        class="rounded-lg border border-border bg-surface-raised px-2 py-1 text-sm text-text outline-none focus:border-brand"
      >
        {#each sizes as size (size)}
          <option value={size}>{size}</option>
        {/each}
        <!-- A ?size= somebody typed, or a list whose own default is off the menu: show the
             value in force rather than snapping the control to something it is not. -->
        {#if !sizes.includes(limit)}
          <option value={limit}>{limit}</option>
        {/if}
      </select>
    </label>

    <!-- Stepping controls only. Held-open muted arrows exist so the row does not jump
         sideways as you page; over a single page there is no paging to hold a place in,
         and a lone highlighted "1" repeats what the range already said. -->
    {#if steppable}
      <div class="flex items-center gap-1">
        {#if page > 1}
          <a href={href(1)} class="{linkClass} {desktop}" aria-label={t("table.paging.first")}>
            <ChevronsLeft size={15} />
          </a>
          <a
            href={href(page - 1)}
            class="{linkClass} inline-flex"
            aria-label={t("common.previous")}
          >
            <ChevronLeft size={15} />
          </a>
        {:else}
          <!-- Held open rather than dropped: the row must not jump sideways as you page. -->
          <span class="{mutedClass} {desktop}" aria-hidden="true"><ChevronsLeft size={15} /></span>
          <span class="{mutedClass} inline-flex" aria-hidden="true"><ChevronLeft size={15} /></span>
        {/if}

        <span class="whitespace-nowrap px-1 text-text-muted sm:hidden">
          {t("table.paging.page_of", { page, pages })}
        </span>

        {#each steps as target, i (i)}
          {#if target === null}
            <span class="{mutedClass} {desktop}" aria-hidden="true">…</span>
          {:else if target === page}
            <span
              class="{boxClass} {desktop} rounded-lg border border-brand bg-brand/10 font-medium text-brand"
              aria-current="page"
            >
              {target}
            </span>
          {:else}
            <a
              href={href(target)}
              class="{linkClass} {desktop}"
              aria-label={t("table.paging.go_to", { page: target })}
            >
              {target}
            </a>
          {/if}
        {/each}

        {#if page < pages}
          <a href={href(page + 1)} class="{linkClass} inline-flex" aria-label={t("common.next")}>
            <ChevronRight size={15} />
          </a>
          <a href={href(pages)} class="{linkClass} {desktop}" aria-label={t("table.paging.last")}>
            <ChevronsRight size={15} />
          </a>
        {:else}
          <span class="{mutedClass} inline-flex" aria-hidden="true"><ChevronRight size={15} /></span
          >
          <span class="{mutedClass} {desktop}" aria-hidden="true"><ChevronsRight size={15} /></span>
        {/if}
      </div>
    {/if}
  </div>
</nav>
