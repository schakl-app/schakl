<script lang="ts">
  /** The client's websites on the company detail page (owner feedback: replaces the hosting
   *  panel there — hosting is shared infrastructure, the websites are the client's). */
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";

  interface PanelWebsite {
    id: string;
    domain_id: string;
    name: string;
    root: boolean;
    hosting_name: string | null;
    uptime_enabled: boolean;
  }

  let { companyId, data }: { companyId: string; data: Record<string, unknown> } = $props();
  const websites = $derived((data.websites ?? []) as PanelWebsite[]);
</script>

{#if websites.length === 0}
  <p class="text-sm text-text-muted">{t("websites.panel.empty")}</p>
{:else}
  <ul class="divide-y divide-border">
    {#each websites as site (site.id)}
      <li class="flex items-center gap-2 py-2">
        <a
          href={`/domains/${site.domain_id}#website`}
          class="min-w-0 flex-1 truncate text-sm font-medium text-text hover:text-brand"
        >
          {site.root ? site.name : `www.${site.name}`}
        </a>
        {#if site.hosting_name}
          <span class="text-xs text-text-muted">{site.hosting_name}</span>
        {/if}
        {#if site.uptime_enabled}
          <span
            class="rounded-full bg-green-500/10 px-2 py-0.5 text-[11px] text-green-700 dark:text-green-400"
          >
            {t("websites.uptime_short")}
          </span>
        {/if}
      </li>
    {/each}
  </ul>
{/if}
{#if can(page.data.user, "websites.website.write")}
  <!-- Quick-create from the client page: opens the website dialog narrowed to this client. -->
  <a
    href={`/websites?company=${companyId}&new=1`}
    class="mt-3 inline-block text-xs text-brand hover:underline"
  >
    ＋ {t("websites.new")}
  </a>
{/if}
