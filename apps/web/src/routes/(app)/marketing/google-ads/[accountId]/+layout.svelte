<script lang="ts">
  /**
   * The section chrome for one Google Ads account: which account, and the tabs.
   *
   * The tab row moved here when the policy and the decisions log became sub-routes rather than
   * `?view=` tabs. They are sub-routes because both are screens the user *writes* on — a form and
   * a list with actions — and the report page's streamed-promise shape exists to keep a table
   * from blanking on refresh, which is the wrong shape for a form.
   */
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";

  let { data, children } = $props();

  const REPORTS = ["trend", "campaigns", "keywords", "search-terms", "negatives", "changes"];

  const base = $derived(`/marketing/google-ads/${page.params.accountId}`);
  const view = $derived(page.url.searchParams.get("view") ?? "campaigns");
  const period = $derived(page.url.searchParams.get("period") ?? "30d");
  const onReports = $derived(page.url.pathname === base);

  function reportHref(name: string): string {
    const params = new URLSearchParams();
    if (name !== "campaigns") params.set("view", name);
    if (period !== "30d") params.set("period", period);
    const qs = params.toString();
    return `${base}${qs ? `?${qs}` : ""}`;
  }

  const tabClass = (active: boolean) =>
    `rounded-lg px-3 py-1.5 text-sm font-medium ${
      active ? "bg-brand text-white" : "text-text-muted hover:bg-surface"
    }`;
</script>

<!-- Sub-route tabs at the very top of the section, above the heading (docs/UX.md, Navigation). -->
<nav class="mb-4 flex flex-wrap gap-1" aria-label={t("google_ads.nav.reports")}>
  {#each REPORTS as name (name)}
    <a href={reportHref(name)} class={tabClass(onReports && view === name)}>
      {t(`google_ads.view.${name.replace("-", "_")}`)}
    </a>
  {/each}
  <a href="{base}/decisions" class={tabClass(page.url.pathname.endsWith("/decisions"))}>
    {t("google_ads.view.decisions")}
  </a>
  <!--
    Mirrors the API's own key, never `!isPortal` and never a broader one: `GET /policy` declares
    `google_ads.policy.manage`, so a member who holds only `account.read` is offered a tab that
    would answer 403 (#310 — a control whose gate and whose call disagree is a screen that cannot
    explain itself).
  -->
  {#if can(page.data.user, "google_ads.policy.manage")}
    <a href="{base}/policy" class={tabClass(page.url.pathname.endsWith("/policy"))}>
      {t("google_ads.view.policy")}
    </a>
  {/if}
</nav>

<div class="mb-4">
  <h1 class="text-xl font-semibold text-text">{data.account.descriptive_name}</h1>
  <p class="mt-1 text-sm text-text-muted">
    {data.account.customer_id_formatted}
    {#if data.account.currency_code}· {data.account.currency_code}{/if}
    {#if data.account.time_zone}· {data.account.time_zone}{/if}
  </p>
</div>

{@render children()}
