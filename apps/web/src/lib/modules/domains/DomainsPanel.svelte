<script lang="ts">
  /** Domains attached to a client, shown on the company detail page (issue #90). */
  import { page } from "$app/state";
  import { fmtMoney, fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";

  interface PanelDomain {
    id: string;
    name: string;
    status: string;
    email_enabled: boolean;
    has_website?: boolean;
    next_invoice_date?: string | null;
    resolved_price?: string | null;
    resolved_currency?: string | null;
  }

  let { companyId, data }: { companyId: string; data: Record<string, unknown> } = $props();
  const domains = $derived((data.domains ?? []) as PanelDomain[]);
  // The API caps the rows and sends the whole count beside them, so the card can say how much
  // it is *not* showing. Falls back to the rows on hand, which is what an older API would send.
  const total = $derived((data.total as number | undefined) ?? domains.length);
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
        <div class="min-w-0 flex-1">
          <a
            href="/domains/{domain.id}"
            class="block truncate text-sm font-medium text-brand hover:underline">{domain.name}</a
          >
          <!-- Renewal + resolved price (#250): what it costs and when it next bills. -->
          {#if domain.next_invoice_date || domain.resolved_price != null}
            <span class="mt-0.5 block truncate text-xs text-text-muted">
              {#if domain.resolved_price != null}
                {t("domains.price_per_year", {
                  amount: fmtMoney(Number(domain.resolved_price)),
                })}
              {/if}
              {#if domain.next_invoice_date}
                {#if domain.resolved_price != null}·{/if}
                {t("domains.renewal")}: {fmtNumericDate(domain.next_invoice_date)}
              {/if}
            </span>
          {/if}
        </div>
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
<div class="mt-3 flex items-center gap-4">
  {#if total > domains.length}
    <!-- The card shows the first five; the register shows the rest. Same filter and same
         default sort at the other end (`domains/panels.py`), so this continues the list rather
         than opening a differently-ordered one — and the count says how much is behind it,
         because five rows with nothing to contradict them read as the whole answer. -->
    <a href={`/domains?company=${companyId}`} class="text-xs text-brand hover:underline">
      {t("domains.panel.view_all", { count: total })}
    </a>
  {/if}
  {#if can(page.data.user, "domains.domain.write")}
    <!-- Quick-create from the client page: opens the domain dialog with this client set. -->
    <a href={`/domains?company=${companyId}&new=1`} class="text-xs text-brand hover:underline">
      ＋ {t("domains.new")}
    </a>
  {/if}
</div>
