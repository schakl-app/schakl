<script lang="ts">
  /**
   * Instellingen → Marketing (#134): the org's Google Ads developer token.
   *
   * A per-agency secret Google Ads needs on every call — stored encrypted per-org (not env config),
   * so a self-hoster sets it here rather than editing the environment. Write-only, mirroring the
   * Google client secret: the API reports only whether one is configured and never returns it.
   */
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import Button from "$lib/core/ui/Button.svelte";
  import FormCheckbox from "$lib/core/ui/FormCheckbox.svelte";
  import { compareModeLabel } from "$lib/modules/marketing/format";
  import { COMPARE_PERIODS } from "$lib/modules/marketing/types";

  let { data, form } = $props();
  const settings = $derived(data.settings);

  const busy = new InFlight();

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<svelte:head>
  <title>{pageTitle(t("settings.marketing.title"))}</title>
</svelte:head>

<h1 class="mb-1 mt-2 text-xl font-semibold text-text">{t("settings.marketing.title")}</h1>
<p class="mb-6 text-sm text-text-muted">{t("settings.marketing.subtitle")}</p>

<section class="max-w-2xl rounded-xl border border-border bg-surface-raised p-5">
  <!-- keep(): this edits settings that already exist. The two secrets load empty by design, but
       the comparison select carries a real saved value that a reset would rewind to the first
       option (docs/UX.md, "Saving must never blank the form"). -->
  <form method="POST" action="?/save" use:enhance={busy.keep()} class="space-y-5">
    <div>
      <label for="ads-developer-token" class="mb-1 block text-sm font-medium text-text">
        {t("settings.marketing.ads_developer_token")}
      </label>
      <input
        id="ads-developer-token"
        name="ads_developer_token"
        type="password"
        autocomplete="new-password"
        placeholder={settings?.ads_developer_token_configured
          ? t("settings.marketing.token_configured")
          : ""}
        class={inputClass}
      />
      <p class="mt-1 text-xs text-text-muted">{t("settings.marketing.ads_developer_token_hint")}</p>
      {#if settings?.env_ads_token_configured && !settings?.ads_developer_token_configured}
        <p class="mt-1 text-xs text-text-muted">{t("settings.marketing.env_fallback_hint")}</p>
      {/if}
    </div>

    <div class="border-t border-border pt-5">
      <label for="seranking-api-key" class="mb-1 block text-sm font-medium text-text">
        {t("marketing.settings.seranking_key")}
      </label>
      <input
        id="seranking-api-key"
        name="seranking_api_key"
        type="password"
        autocomplete="new-password"
        placeholder={settings?.seranking_api_key_configured
          ? t("marketing.settings.seranking_key_configured")
          : ""}
        class={inputClass}
      />
      <p class="mt-1 text-xs text-text-muted">{t("marketing.settings.seranking_key_hint")}</p>
    </div>

    <!-- The agency's house comparison (#312). A client's own dashboard overrides it in its edit
         mode; this is what the other fifty-nine clients inherit without anyone touching them. -->
    <div class="border-t border-border pt-5">
      <label for="default-compare" class="mb-1 block text-sm font-medium text-text">
        {t("settings.marketing.default_compare")}
      </label>
      <select
        id="default-compare"
        name="default_compare"
        value={settings?.default_compare ?? "year"}
        class={inputClass}
      >
        {#each COMPARE_PERIODS as mode (mode)}
          <option value={mode}>{compareModeLabel(mode)}</option>
        {/each}
      </select>
      <p class="mt-1 text-xs text-text-muted">{t("settings.marketing.default_compare_hint")}</p>
    </div>

    <!-- The house rule for keyword positions (#373). Every client's report inherits this; the
         one whose situation differs overrides it on their own reporting page. "Automatisch" is
         the default and the only value that is right for a mixed client list without anyone
         visiting a screen: SE Ranking where the client has a project, Search Console otherwise. -->
    <div class="border-t border-border pt-5">
      <h3 class="mb-1 text-sm font-semibold text-text">{t("settings.marketing.rankings")}</h3>
      <p class="mb-3 text-xs text-text-muted">{t("settings.marketing.rankings_hint")}</p>
      <div class="grid gap-4 sm:grid-cols-3">
        <div>
          <label for="rankings-source" class="mb-1 block text-sm font-medium text-text">
            {t("reporting.rankings.source")}
          </label>
          <select
            id="rankings-source"
            name="rankings_source"
            value={settings?.rankings?.source ?? "auto"}
            class={inputClass}
          >
            <option value="auto">{t("reporting.rankings.source_auto")}</option>
            <option value="seranking">{t("reporting.rankings.source_seranking")}</option>
            <option value="search_console">{t("reporting.rankings.source_search_console")}</option>
            <option value="off">{t("reporting.rankings.source_off")}</option>
          </select>
        </div>
        <div>
          <label for="rankings-limit" class="mb-1 block text-sm font-medium text-text">
            {t("reporting.rankings.limit")}
          </label>
          <input
            id="rankings-limit"
            name="rankings_limit"
            type="number"
            min="1"
            max="200"
            value={settings?.rankings?.limit ?? 25}
            class={inputClass}
          />
          <p class="mt-1 text-xs text-text-muted">{t("reporting.rankings.limit_hint")}</p>
        </div>
        <div>
          <label for="rankings-impressions" class="mb-1 block text-sm font-medium text-text">
            {t("reporting.rankings.min_impressions")}
          </label>
          <input
            id="rankings-impressions"
            name="rankings_min_impressions"
            type="number"
            min="0"
            max="10000"
            value={settings?.rankings?.min_impressions ?? 10}
            class={inputClass}
          />
          <p class="mt-1 text-xs text-text-muted">{t("reporting.rankings.min_impressions_hint")}</p>
        </div>
      </div>
      <div class="mt-3 space-y-2">
        <label class="flex items-center gap-2 text-sm text-text">
          <FormCheckbox
            name="rankings_grouped"
            checked={settings?.rankings?.grouped ?? true}
            class="rounded border-border"
          />
          <span>{t("reporting.rankings.grouped")}</span>
        </label>
        <label class="flex items-center gap-2 text-sm text-text">
          <FormCheckbox
            name="rankings_show_landing_pages"
            checked={settings?.rankings?.show_landing_pages ?? true}
            class="rounded border-border"
          />
          <span>{t("reporting.rankings.show_landing_pages")}</span>
        </label>
      </div>
    </div>

    {#if form?.saved}
      <p class="text-sm text-green-600 dark:text-green-400">{t("settings.marketing.saved")}</p>
    {:else if form?.error}
      <p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
    {/if}

    <Button type="submit" loading={busy.active}>
      {t("common.save")}
    </Button>
  </form>
</section>
