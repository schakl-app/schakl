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
   */
  import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from "@lucide/svelte";

  import { goto } from "$app/navigation";
  import { page as pageState } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { PAGE_SIZES, pageCount, pageHref, pageWindow } from "$lib/core/table/paging";

  let {
    total,
    page,
    limit,
    onsize,
    sizes = PAGE_SIZES as readonly number[],
  }: {
    /** The API's count of the whole set — never `rows.length`, which counts this page (#37). */
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

  /** Rendered only when there is more than one page to be on — otherwise it is decoration. */
  const shown = $derived(pages > 1 || page > 1);

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

{#if shown}
  <nav
    class="mt-4 flex flex-col gap-3 text-sm sm:flex-row sm:items-center sm:justify-between"
    aria-label={t("table.paging.label")}
    data-sveltekit-preload-data="hover"
  >
    <span class="text-text-muted">
      {t("table.paging.range", { from, to, total })}
    </span>

    <div class="flex items-center justify-between gap-3 sm:justify-end">
      <label class="flex items-center gap-2 text-text-muted">
        <span class="hidden sm:inline">{t("table.paging.size")}</span>
        <select
          value={limit}
          onchange={chooseSize}
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
    </div>
  </nav>
{/if}
