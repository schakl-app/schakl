<script lang="ts">
  /**
   * The app-wide breadcrumb row (owner request): rendered once by the layout above every page, so
   * no screen can ship without one. Items come from core/breadcrumbs.
   *
   * Two things keep the row short, because a breadcrumb that wraps onto a second line has stopped
   * being chrome and started being content.
   *
   * **Length is capped, not thrown away.** Past `max` crumbs the middle folds into a "…" the
   * visitor can open — a trail that silently dropped its ancestors would be worse than a long one,
   * since those crumbs are the only link to the records they name. The button carries the hidden
   * labels in its `title`, so the answer is available on hover before it is available on click.
   *
   * **A label is clipped, never rewritten.** A client called "Stichting Openbare Bibliotheken
   * Noord-Holland" gets a width cap and its full text in `title`; shortening it in JavaScript
   * would mean deciding where a name may break, which is a decision no locale wins.
   */
  import type { Crumb } from "$lib/core/breadcrumbs";
  import { t } from "$lib/core/i18n";

  let { crumbs, max = 4 }: { crumbs: Crumb[]; max?: number } = $props();

  /**
   * Which row the visitor opened, rather than a bare "is it open" flag: a new page is a new row,
   * and an expansion asked for on the previous screen must not survive onto this one. Storing the
   * row it applies to resets it by construction — an effect that blanked a flag would have to run
   * *after* the row changed, which is one render of the wrong thing.
   */
  let expandedRow = $state<string | null>(null);
  const row = $derived(crumbs.map((crumb) => crumb.label).join("›"));

  const collapsed = $derived(expandedRow !== row && crumbs.length > max);
  /** Everything between the first crumb and the last two — the stretch with the least to say. */
  const hidden = $derived(collapsed ? crumbs.slice(1, crumbs.length - 2) : []);
  const head = $derived(collapsed ? crumbs.slice(0, 1) : crumbs);
  const tail = $derived(collapsed ? crumbs.slice(-2) : []);
</script>

{#snippet item(crumb: Crumb, first: boolean)}
  <li class="flex min-w-0 items-center gap-1">
    {#if !first}
      <span class="text-text-muted/60" aria-hidden="true">›</span>
    {/if}
    {#if crumb.href}
      <a
        href={crumb.href}
        title={crumb.label}
        class="max-w-[9rem] truncate text-text-muted hover:text-text sm:max-w-[14rem]"
      >
        {crumb.label}
      </a>
    {:else}
      <span
        title={crumb.label}
        class="max-w-[11rem] truncate font-medium text-text sm:max-w-[20rem]"
        aria-current="page"
      >
        {crumb.label}
      </span>
    {/if}
  </li>
{/snippet}

<nav aria-label={t("breadcrumbs.label")} class="mb-4 min-w-0">
  <ol class="flex flex-wrap items-center gap-1 text-sm">
    {#each head as crumb, i (i)}
      {@render item(crumb, i === 0)}
    {/each}
    {#if hidden.length > 0}
      <li class="flex items-center gap-1">
        <span class="text-text-muted/60" aria-hidden="true">›</span>
        <button
          type="button"
          class="rounded px-1 text-text-muted hover:text-text"
          title={hidden.map((crumb) => crumb.label).join(" › ")}
          aria-label={t("breadcrumbs.expand")}
          onclick={() => (expandedRow = row)}
        >
          …
        </button>
      </li>
    {/if}
    {#each tail as crumb, i (i)}
      {@render item(crumb, false)}
    {/each}
  </ol>
</nav>
