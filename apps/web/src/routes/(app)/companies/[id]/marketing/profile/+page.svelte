<script lang="ts">
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import LeadProfileEditor from "$lib/modules/marketing/leads/LeadProfileEditor.svelte";
  import type { LeadProfile, LeadsCatalog } from "$lib/modules/marketing/leads/types";

  let { data, form } = $props();

  const company = $derived(data.company);
  // The catalog streams (docs/PERFORMANCE.md): the form is usable before Google answers, and
  // the datalists fill in when it does.
  let catalog = $state<LeadsCatalog | null>(null);
  let catalogPending = $state(true);
  $effect(() => {
    const promise = data.catalog;
    catalogPending = true;
    void promise.then((value) => {
      if (data.catalog !== promise) return;
      catalog = value as LeadsCatalog | null;
      catalogPending = false;
    });
  });
</script>

<svelte:head>
  <title>{pageTitle(`${company.name} · ${t("marketing.leads.profile.title")}`)}</title>
</svelte:head>

<div class="mb-6">
  <a href={`/companies/${company.id}/marketing`} class="text-sm text-text-muted hover:text-text">
    ← {t("marketing.tab.title")}
  </a>
  <h1 class="mt-2 text-xl font-semibold text-text">{t("marketing.leads.profile.title")}</h1>
  <p class="mt-1 text-sm text-text-muted">{t("marketing.leads.profile.subtitle")}</p>
</div>

<LeadProfileEditor
  companyId={company.id}
  profile={(data.settings?.lead_profile ?? null) as LeadProfile | null}
  links={data.settings?.links ?? []}
  {catalog}
  {catalogPending}
  saved={Boolean(form?.saved)}
  error={form?.error ?? null}
  errorDetail={form?.detail ?? null}
/>
