<script lang="ts">
  /**
   * "Mijn domeinnamen" — the client's domain names on their own homepage.
   *
   * The register as the client sees it: the name, what it does (active, a redirect, parked),
   * and when it renews. Never the registrar, the DNS provider or a price — the supplier behind
   * the agency's service is not the client's business (the same rule the domains list applies to
   * its columns for an external login). Read on the selected company; the horizon scopes it.
   */
  import { fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import DashboardWidgetCard from "$lib/core/ui/DashboardWidgetCard.svelte";
  import PanelRows from "$lib/core/ui/PanelRows.svelte";

  let { data }: { data: unknown } = $props();

  interface Row {
    id: string;
    name: string;
    status: string;
    register_expires_on: string | null;
    next_invoice_date: string | null;
  }
  interface Payload {
    items: Row[];
    total: number;
    companyId: string | null;
  }
  const EMPTY: Payload = { items: [], total: 0, companyId: null };
  const payload = $derived((data ?? EMPTY) as Payload);
  const href = $derived(payload.companyId ? `/domains?company=${payload.companyId}` : "/domains");

  function statusClass(status: string): string {
    if (status === "active") return "bg-emerald-50 text-emerald-700";
    if (status === "expired") return "bg-red-50 text-red-700";
    return "bg-surface text-text-muted";
  }
</script>

<DashboardWidgetCard
  title={t("dashboard.widget.domains.portal")}
  {href}
  linkLabel={t("nav.domains")}
>
  {#if payload.items.length === 0}
    <p class="text-sm text-text-muted">{t("domains.portal.empty")}</p>
  {:else}
    <PanelRows rows={payload.items} collapsed={8} total={payload.total} {href}>
      {#snippet children(shown)}
        <ul class="divide-y divide-border">
          {#each shown as domain (domain.id)}
            {@const renews = domain.register_expires_on ?? domain.next_invoice_date}
            <li class="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
              <a
                href={`/domains/${domain.id}`}
                class="min-w-0 flex-1 truncate text-sm font-medium text-text hover:text-brand"
                >{domain.name}</a
              >
              {#if renews}
                <span class="text-xs text-text-muted">
                  {t("domains.portal.renews_on")}: {fmtNumericDate(renews)}
                </span>
              {/if}
              <span class="rounded-md px-2 py-0.5 text-xs {statusClass(domain.status)}"
                >{t(`domains.status.${domain.status}`)}</span
              >
            </li>
          {/each}
        </ul>
      {/snippet}
    </PanelRows>
  {/if}
</DashboardWidgetCard>
