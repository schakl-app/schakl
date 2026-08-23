<script lang="ts">
  /** The client's websites on the company detail page (owner feedback: replaces the hosting
   *  panel there — hosting is shared infrastructure, the websites are the client's). */
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { uptimeChipClass, uptimeLabel, uptimeState } from "$lib/modules/websites/uptime";
  import { can } from "$lib/core/permissions";
  import PanelRows from "$lib/core/ui/PanelRows.svelte";

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
  // The API caps the rows and sends the whole count beside them (`websites/panels.py`), so the
  // card can say how much it is *not* showing rather than implying it shows everything.
  const total = $derived((data.total as number | undefined) ?? websites.length);
</script>

<!-- Five here, the rest one click away on the same filter and the same sort — the card is the
       first page of the list it links to, never a sample of a different one (#407). -->
<PanelRows
  rows={websites}
  {total}
  href={`/websites?company=${companyId}`}
  linkLabel={t("websites.panel.view_all", { count: total })}
>
  {#snippet children(shown)}
    {#if shown.length === 0}
      <p class="text-sm text-text-muted">{t("websites.panel.empty")}</p>
    {:else}
      <ul class="divide-y divide-border">
        {#each shown as site (site.id)}
          <li class="flex items-center gap-2 py-2">
            <a
              href={`/websites/${site.id}`}
              class="min-w-0 flex-1 truncate text-sm font-medium text-text hover:text-brand"
            >
              {site.root ? site.name : `www.${site.name}`}
            </a>
            {#if site.hosting_name}
              <span class="text-xs text-text-muted">{site.hosting_name}</span>
            {/if}
            {#if uptimeState(site)}
              {@const state = uptimeState(site)!}
              <span class="rounded-full px-2 py-0.5 text-[11px] {uptimeChipClass(state)}">
                {uptimeLabel(state)}
              </span>
            {/if}
          </li>
        {/each}
      </ul>
    {/if}
  {/snippet}
  {#snippet actions()}
    {#if can(page.data.user, "websites.website.write")}
      <!-- Quick-create from the client page: opens the dialog narrowed to this client. -->
      <a href={`/websites?company=${companyId}&new=1`} class="text-brand hover:underline">
        ＋ {t("websites.new")}
      </a>
    {/if}
  {/snippet}
</PanelRows>
