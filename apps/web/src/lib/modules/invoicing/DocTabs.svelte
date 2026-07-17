<script lang="ts">
  /** Facturen | Offertes — the submenu-tabs pattern for a section with two surfaces
   * (docs/UX.md, Navigation). Rendered by both list pages so they read as one section. */
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";

  let { showQuotes = true }: { showQuotes?: boolean } = $props();

  const tabs = $derived([
    { href: "/invoices", label: t("invoicing.invoices") },
    ...(showQuotes ? [{ href: "/quotes", label: t("invoicing.quotes") }] : []),
  ]);
</script>

<nav class="mb-4 flex gap-1 border-b border-border" aria-label={t("invoicing.title")}>
  {#each tabs as tab (tab.href)}
    <a
      href={tab.href}
      data-sveltekit-preload-data="hover"
      class="border-b-2 px-3 py-2 text-sm font-medium {page.url.pathname.startsWith(tab.href)
        ? 'border-brand text-brand'
        : 'border-transparent text-text-muted hover:text-text'}"
      aria-current={page.url.pathname.startsWith(tab.href) ? "page" : undefined}
    >
      {tab.label}
    </a>
  {/each}
</nav>
