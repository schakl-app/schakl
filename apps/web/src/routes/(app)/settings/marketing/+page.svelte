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
  import { compareModeLabel, portalDefaultLabel, sourceLabel } from "$lib/modules/marketing/format";
  import { COMPARE_PERIODS, PORTAL_LABEL_SOURCES } from "$lib/modules/marketing/types";

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

    <!-- What a client is told each source is called (#446). The supplier behind the agency's
         service is not the client's business, so a keyed source (SE Ranking, Rank Math) is
         named for what it *measures* by default and the tenant may put their own product name
         on it — "Breik. Analytics" is one tenant's word and lives here, never in code (§2). A
         Google source keeps the product name: it is the client's own account. -->
    <fieldset class="border-t border-border pt-5">
      <legend class="mb-1 text-sm font-semibold text-text">
        {t("settings.marketing.portal_labels")}
      </legend>
      <p class="mb-3 text-xs text-text-muted">{t("settings.marketing.portal_labels_hint")}</p>
      <div class="grid gap-3 sm:grid-cols-2">
        {#each PORTAL_LABEL_SOURCES as source (source)}
          <div>
            <label for={`portal-label-${source}`} class="mb-1 block text-xs text-text-muted">
              {sourceLabel(source)}
            </label>
            <input
              id={`portal-label-${source}`}
              name={`portal_label_${source}`}
              value={settings?.portal_source_labels?.[source] ?? ""}
              placeholder={portalDefaultLabel(source)}
              maxlength="80"
              class={inputClass}
            />
          </div>
        {/each}
      </div>
    </fieldset>

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
      <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
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
        <div>
          <label for="rankings-depth" class="mb-1 block text-sm font-medium text-text">
            {t("reporting.rankings.max_position")}
          </label>
          <input
            id="rankings-depth"
            name="rankings_max_position"
            type="number"
            min="3"
            max="100"
            value={settings?.rankings?.max_position ?? 25}
            class={inputClass}
          />
          <p class="mt-1 text-xs text-text-muted">{t("reporting.rankings.max_position_hint")}</p>
        </div>
      </div>
      <div class="mt-4">
        <label for="report-split" class="mb-1 block text-sm font-medium text-text">
          {t("reporting.websites.split")}
        </label>
        <select
          id="report-split"
          name="report_split"
          value={settings?.report?.split ?? "per_website"}
          class={inputClass}
        >
          <option value="per_website">{t("reporting.websites.split_per_website")}</option>
          <option value="combined">{t("reporting.websites.split_combined")}</option>
        </select>
        <p class="mt-1 text-xs text-text-muted">{t("reporting.websites.split_hint")}</p>
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
