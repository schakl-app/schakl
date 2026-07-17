<script lang="ts">
  /** Domains attached to a client, shown on the company detail page (issue #90). */
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";

  interface PanelDomain {
    id: string;
    name: string;
    status: string;
    email_enabled: boolean;
    has_website?: boolean;
  }

  let { companyId, data }: { companyId: string; data: Record<string, unknown> } = $props();
  const domains = $derived((data.domains ?? []) as PanelDomain[]);
  // A website is the 0/1 child of a domain, so its quick link lives on the domain row: open the
  // existing one, or add one right there — everything for a client starts from the client page.
  const websitesEnabled = $derived(
    ((page.data.theme?.enabledModules ?? []) as string[]).includes("websites"),
  );
</script>

{#if domains.length === 0}
  <p class="text-sm text-text-muted">{t("domains.panel.empty")}</p>
{:else}
  <ul class="divide-y divide-border">
    {#each domains as domain (domain.id)}
      <li class="flex items-center gap-2 py-2">
        <a
          href="/domains/{domain.id}"
          class="min-w-0 flex-1 truncate text-sm font-medium text-brand hover:underline"
          >{domain.name}</a
        >
        {#if websitesEnabled}
          {#if domain.has_website}
            <a
              href="/domains/{domain.id}#website"
              class="shrink-0 text-xs text-text-muted hover:text-brand hover:underline"
            >
              {t("websites.title")}
            </a>
          {:else if can(page.data.user, "websites.website.write")}
            <a
              href="/domains/{domain.id}#website"
              class="shrink-0 text-xs text-brand hover:underline"
            >
              ＋ {t("domains.panel.add_website")}
            </a>
          {/if}
        {/if}
        <span class="rounded-md bg-surface px-2 py-0.5 text-xs text-text-muted"
          >{t(`domains.status.${domain.status}`)}</span
        >
      </li>
    {/each}
  </ul>
{/if}
{#if can(page.data.user, "domains.domain.write")}
  <!-- Quick-create from the client page: opens the domain dialog with this client set. -->
  <a
    href={`/domains?company=${companyId}&new=1`}
    class="mt-3 inline-block text-xs text-brand hover:underline"
  >
    ＋ {t("domains.new")}
  </a>
{/if}
