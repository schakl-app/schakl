<script lang="ts">
  /** The app-wide breadcrumb row (owner request): rendered once by the layout above every
   *  page, so no screen can ship without one. Items come from core/breadcrumbs. */
  import type { Crumb } from "$lib/core/breadcrumbs";

  let { crumbs }: { crumbs: Crumb[] } = $props();
</script>

<nav aria-label="Breadcrumb" class="mb-4 min-w-0">
  <ol class="flex flex-wrap items-center gap-1 text-sm">
    {#each crumbs as crumb, i (i)}
      <li class="flex min-w-0 items-center gap-1">
        {#if i > 0}
          <span class="text-text-muted/60" aria-hidden="true">›</span>
        {/if}
        {#if crumb.href}
          <a href={crumb.href} class="truncate text-text-muted hover:text-text">{crumb.label}</a>
        {:else}
          <span class="truncate font-medium text-text" aria-current="page">{crumb.label}</span>
        {/if}
      </li>
    {/each}
  </ol>
</nav>
