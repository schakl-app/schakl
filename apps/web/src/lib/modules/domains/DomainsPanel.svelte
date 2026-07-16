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
  }

  let { companyId, data }: { companyId: string; data: Record<string, unknown> } = $props();
  const domains = $derived((data.domains ?? []) as PanelDomain[]);
</script>

{#if domains.length === 0}
  <p class="text-sm text-text-muted">{t("domains.panel.empty")}</p>
{:else}
  <ul class="divide-y divide-border">
    {#each domains as domain (domain.id)}
      <li class="flex items-center gap-2 py-2">
        <a href="/domains/{domain.id}" class="flex-1 text-sm font-medium text-brand hover:underline"
          >{domain.name}</a
        >
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
