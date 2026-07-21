<script lang="ts">
  /**
   * Sub-route tabs over the subscriptions section (#229): the live list, the standard
   * subscriptions and the types — the submenu-tabs convention (docs/UX.md, Navigation),
   * same shape as /overview. Members without catalog permissions get no tab row: a single
   * tab is noise, and the list page is then the whole section.
   */
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import { navLabel } from "$lib/core/title";

  let { children } = $props();

  const path = $derived(page.url.pathname);
  const canRead = $derived(can(page.data.user, "subscriptions.subscription.read"));
  const canTemplates = $derived(can(page.data.user, "subscriptions.template.manage"));
  const canTypes = $derived(can(page.data.user, "subscriptions.type.manage"));
  const tabClass = (active: boolean) =>
    `rounded-lg px-3 py-1.5 text-sm font-medium ${
      active ? "bg-brand text-white" : "text-text-muted hover:bg-surface"
    }`;
</script>

{#if canTemplates || canTypes}
  <div class="mb-4 flex flex-wrap items-center gap-1" data-sveltekit-preload-data="hover">
    {#if canRead}
      <a href="/subscriptions" class={tabClass(path === "/subscriptions")}>
        {navLabel("subscriptions", t("subscriptions.title"))}
      </a>
    {/if}
    {#if canTemplates}
      <a
        href="/subscriptions/templates"
        class={tabClass(path.startsWith("/subscriptions/templates"))}
      >
        {t("settings.subscriptions.templates_heading")}
      </a>
    {/if}
    {#if canTypes}
      <a href="/subscriptions/types" class={tabClass(path.startsWith("/subscriptions/types"))}>
        {t("settings.subscriptions.types_heading")}
      </a>
    {/if}
  </div>
{/if}

{@render children()}
