<script lang="ts">
  /** The section chrome for one Meta ad account: which account, and the tabs. */
  import ExternalLink from "@lucide/svelte/icons/external-link";

  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { PAGE_TITLE } from "$lib/core/ui/headings";

  let { data, children } = $props();

  const base = $derived(`/marketing/meta-ads/${page.params.accountId}`);
  const path = $derived(page.url.pathname);

  const tabClass = (active: boolean) =>
    `rounded-lg px-3 py-1.5 text-sm font-medium ${
      active ? "bg-brand text-white" : "text-text-muted hover:bg-surface"
    }`;
</script>

<!-- Sub-route tabs at the very top of the section, above the heading (docs/UX.md, Navigation). -->
<nav class="mb-4 flex flex-wrap gap-1" aria-label={t("meta_ads.nav.label")}>
  <a href={base} class={tabClass(path === base)}>{t("meta_ads.view.campaigns")}</a>
  <a href="{base}/decisions" class={tabClass(path.endsWith("/decisions"))}>
    {t("meta_ads.view.decisions")}
  </a>
  <!-- Mirrors the key the screen's own save makes (#310). Reading the policy is open to any
       reader of the account, but a tab that is a form nobody may submit is a broken control. -->
  {#if data.canPolicy}
    <a href="{base}/policy" class={tabClass(path.endsWith("/policy"))}>
      {t("meta_ads.view.policy")}
    </a>
  {/if}
</nav>

<div class="mb-5 flex flex-wrap items-start justify-between gap-3">
  <div class="min-w-0">
    <h1 class={PAGE_TITLE}>{data.account.name}</h1>
    <p class="mt-1 text-sm text-text-muted">
      {[
        data.account.company_name ?? t("meta.own_channels"),
        data.account.meta_id,
        data.account.currency,
      ]
        .filter(Boolean)
        .join(" · ")}
    </p>
  </div>
  <a
    href={data.account.ads_manager_url}
    target="_blank"
    rel="noopener noreferrer"
    class="inline-flex items-center gap-1 text-sm text-brand hover:underline"
  >
    {t("meta_ads.page.open_ads_manager")}
    <ExternalLink size={14} aria-hidden="true" />
  </a>
</div>

{@render children()}
