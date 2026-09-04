<script lang="ts">
  /** The client's websites on the company detail page (owner feedback: replaces the hosting
   *  panel there — hosting is shared infrastructure, the websites are the client's). */
  import { page } from "$app/state";
  import { t, tn } from "$lib/core/i18n";
  import { fromHref } from "$lib/core/origin";
  import { uptimeChipClass, uptimeLabel, uptimeState } from "$lib/modules/websites/uptime";
  import { can } from "$lib/core/permissions";
  import PanelRow from "$lib/core/ui/PanelRow.svelte";
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
  linkLabel={tn("websites.panel.view_all", total)}
>
  {#snippet children(shown)}
    {#if shown.length === 0}
      <p class="text-sm text-text-muted">{t("websites.panel.empty")}</p>
    {:else}
      <ul class="divide-y divide-border">
        {#each shown as site (site.id)}
          <PanelRow
            href={fromHref(`/websites/${site.id}`, page.url)}
            title={site.root ? site.name : `www.${site.name}`}
            meta={site.hosting_name}
          >
            {#snippet trailing()}
              {#if uptimeState(site)}
                {@const state = uptimeState(site)!}
                <span
                  class="shrink-0 rounded-full px-2 py-0.5 text-[11px] {uptimeChipClass(state)}"
                >
                  {uptimeLabel(state)}
                </span>
              {/if}
            {/snippet}
          </PanelRow>
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
