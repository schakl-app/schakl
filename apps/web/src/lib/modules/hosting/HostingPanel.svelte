<script lang="ts">
  /** Hosting attached to a client, shown on the company detail page (issue #93). */
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";

  interface PanelHosting {
    id: string;
    name: string;
    provider_name: string | null;
    ip_address: string | null;
  }

  let { companyId, data }: { companyId: string; data: Record<string, unknown> } = $props();
  const hosting = $derived((data.hosting ?? []) as PanelHosting[]);
</script>

{#if hosting.length === 0}
  <p class="text-sm text-text-muted">{t("hosting.panel.empty")}</p>
{:else}
  <ul class="divide-y divide-border">
    {#each hosting as item (item.id)}
      <li class="flex items-center gap-2 py-2">
        <a href="/hosting" class="flex-1 text-sm font-medium text-text">{item.name}</a>
        {#if item.provider_name}
          <span class="text-xs text-text-muted">{item.provider_name}</span>
        {/if}
        {#if item.ip_address}
          <span class="font-mono text-xs text-text-muted">{item.ip_address}</span>
        {/if}
      </li>
    {/each}
  </ul>
{/if}
{#if can(page.data.user, "hosting.hosting.write")}
  <!-- Quick-create from the client page: opens the hosting dialog with this client set. -->
  <a
    href={`/hosting?company=${companyId}&new=1`}
    class="mt-3 inline-block text-xs text-brand hover:underline"
  >
    ＋ {t("hosting.new")}
  </a>
{/if}
