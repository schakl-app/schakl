<script lang="ts">
  /**
   * Instellingen → Marketing (#134): the org's Google Ads developer token.
   *
   * A per-agency secret Google Ads needs on every call — stored encrypted per-org (not env config),
   * so a self-hoster sets it here rather than editing the environment. Write-only, mirroring the
   * Google client secret: the API reports only whether one is configured and never returns it.
   */
  import { enhance } from "$app/forms";
  import { fmtNumber } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import Button from "$lib/core/ui/Button.svelte";
  import FormCheckbox from "$lib/core/ui/FormCheckbox.svelte";
  import {
    AI_ENGINES,
    AI_SCOPES,
    UNITS_PER_ENGINE,
    type AiEngine,
  } from "$lib/modules/marketing/aisearch/types";
  import { compareModeLabel, portalDefaultLabel, sourceLabel } from "$lib/modules/marketing/format";
  import {
    LABELLED_CONNECTIONS,
    COMPARE_PERIODS,
    PORTAL_LABEL_SOURCES,
  } from "$lib/modules/marketing/types";

  let { data, form } = $props();
  const settings = $derived(data.settings);

  const busy = new InFlight();

  // ---- SE Ranking's AI Search overview: the house defaults (docs/SERANKING.md) ---------------
  // Seeded once from what is stored and owned by the form from then on, so the cost line under
  // the boxes follows a tick before anything is saved (#305: show the constraint working).
  // svelte-ignore state_referenced_locally
  const stored = data.settings?.ai_search;
  let aiEnabled = $state(stored?.enabled ?? false);
  let aiEngines = $state<AiEngine[]>([...(stored?.engines ?? ["all"])]);
  function toggleEngine(engine: AiEngine) {
    aiEngines = aiEngines.includes(engine)
      ? aiEngines.filter((e) => e !== engine)
      : [...aiEngines, engine];
  }
  const perClient = $derived(Math.max(aiEngines.length, 1) * UNITS_PER_ENGINE);
  const check = $derived(form?.check ?? null);
  const apiTone = (status: string) =>
    status === "ok"
      ? "text-green-600 dark:text-green-400"
      : status === "not_configured"
        ? "text-text-muted"
        : "text-red-600 dark:text-red-400";

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
  <form
    method="POST"
    action="?/save"
    use:enhance={busy.keep((input) =>
      input.action.search.includes("checkSeranking") ? "check" : "",
    )}
    class="space-y-5"
  >
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

      <!-- SE Ranking sells two APIs and issues a token for each (docs/SERANKING.md §2). One key
           often reaches both, so this box is optional — and the check below says which API each
           key actually reaches, rather than leaving an agency to find out from an empty card. -->
      <label for="seranking-data-api-key" class="mb-1 mt-4 block text-sm font-medium text-text">
        {t("marketing.settings.seranking_data_key")}
      </label>
      <input
        id="seranking-data-api-key"
        name="seranking_data_api_key"
        type="password"
        autocomplete="new-password"
        placeholder={settings?.seranking_data_api_key_configured
          ? t("marketing.settings.seranking_key_configured")
          : t("marketing.settings.seranking_data_key_placeholder")}
        class={inputClass}
      />
      <p class="mt-1 text-xs text-text-muted">{t("marketing.settings.seranking_data_key_hint")}</p>
      {#if settings?.seranking_data_api_key_configured}
        <label class="mt-2 flex items-start gap-2 text-sm text-text">
          <FormCheckbox
            name="clear_seranking_data_api_key"
            class="mt-0.5 shrink-0 rounded border-border"
          />
          <span>{t("marketing.settings.seranking_data_key_clear")}</span>
        </label>
      {/if}

      <div class="mt-3">
        <Button
          type="submit"
          variant="secondary"
          formaction="?/checkSeranking"
          loading={busy.is("check")}
          data-testid="seranking-check"
        >
          {t("marketing.settings.seranking_check")}
        </Button>
        <p class="mt-1 text-xs text-text-muted">{t("marketing.settings.seranking_check_hint")}</p>
      </div>
      {#if check}
        <dl
          class="mt-3 grid gap-x-6 gap-y-2 rounded-lg bg-surface p-4 text-sm sm:grid-cols-2"
          data-testid="seranking-check-result"
        >
          <div>
            <dt class="text-xs text-text-muted">{t("marketing.settings.seranking_project_api")}</dt>
            <dd class="font-medium {apiTone(check.project_api)}">
              {t(`marketing.settings.seranking_api.${check.project_api}`)}
            </dd>
          </div>
          <div>
            <dt class="text-xs text-text-muted">
              {t("marketing.settings.seranking_data_api")}
              · {t(`marketing.settings.seranking_key_used.${check.data_api_key}`)}
            </dt>
            <dd class="font-medium {apiTone(check.data_api)}">
              {t(`marketing.settings.seranking_api.${check.data_api}`)}
            </dd>
          </div>
          {#if check.data_api === "ok" && check.units_left !== null && check.units_left !== undefined}
            <div class="sm:col-span-2">
              <dt class="text-xs text-text-muted">{t("marketing.settings.seranking_units")}</dt>
              <dd class="text-text">
                {t("marketing.settings.seranking_units_value", {
                  left: fmtNumber(check.units_left, 0),
                  limit: fmtNumber(check.units_limit ?? 0, 0),
                })}
                {#if check.monthly_units > 0}
                  <span class="text-text-muted">
                    · {t("marketing.settings.seranking_units_needed", {
                      units: fmtNumber(check.monthly_units, 0),
                      clients: check.enabled_clients,
                    })}
                  </span>
                {/if}
              </dd>
            </div>
          {/if}
          {#if check.data_api === "denied"}
            <p class="text-xs text-text-muted sm:col-span-2">
              {t("marketing.settings.seranking_data_denied_hint")}
            </p>
          {/if}
        </dl>
      {/if}
    </div>

    <!-- The house defaults for SE Ranking's AI Search overview (docs/SERANKING.md). **Off until
         somebody switches it on**: every read costs the agency 800 of its own units per engine
         choice, per client, per month — so the price is printed under the choice that sets it.
         Target and brand are not here: they are facts about one client, edited on that
         client's dashboard. -->
    <fieldset id="ai-search" class="scroll-mt-24 border-t border-border pt-5">
      <legend class="mb-1 text-sm font-semibold text-text">
        {t("settings.marketing.ai_search")}
      </legend>
      <p class="mb-3 text-xs text-text-muted">{t("settings.marketing.ai_search_hint")}</p>
      <label class="flex items-center gap-2 text-sm text-text">
        <!-- The component owns the mark (it survives a reset); the mirror only drives the cost
             line below. -->
        <FormCheckbox
          name="ai_search_enabled"
          checked={aiEnabled}
          onchange={(event) => (aiEnabled = event.currentTarget.checked)}
          class="rounded border-border"
        />
        <span>{t("settings.marketing.ai_search_enabled")}</span>
      </label>
      <div class="mt-4">
        <p class="mb-1 text-sm font-medium text-text">
          {t("marketing.ai_search.settings.engines")}
        </p>
        <div class="flex flex-wrap gap-x-4 gap-y-1.5">
          {#each AI_ENGINES as engine (engine)}
            <label class="flex items-center gap-1.5 text-sm text-text">
              <input
                type="checkbox"
                name="ai_search_engines"
                value={engine}
                checked={aiEngines.includes(engine)}
                onchange={() => toggleEngine(engine)}
                class="rounded border-border"
              />
              {t(`marketing.ai_search.engine.${engine}`)}
            </label>
          {/each}
        </div>
        <p class="mt-1 text-xs text-text-muted">{t("settings.marketing.ai_search_engines_hint")}</p>
      </div>
      <div class="mt-4 grid gap-4 sm:grid-cols-2">
        <div>
          <label for="ai-search-source" class="mb-1 block text-sm font-medium text-text">
            {t("marketing.ai_search.settings.source")}
          </label>
          <input
            id="ai-search-source"
            name="ai_search_source"
            value={settings?.ai_search?.source ?? "nl"}
            maxlength="2"
            class="{inputClass} uppercase"
          />
          <p class="mt-1 text-xs text-text-muted">
            {t("marketing.ai_search.settings.source_hint")}
          </p>
        </div>
        <div>
          <label for="ai-search-scope" class="mb-1 block text-sm font-medium text-text">
            {t("marketing.ai_search.settings.scope")}
          </label>
          <select
            id="ai-search-scope"
            name="ai_search_scope"
            value={settings?.ai_search?.scope ?? "base_domain"}
            class={inputClass}
          >
            {#each AI_SCOPES as scope (scope)}
              <option value={scope}>{t(`marketing.ai_search.scope.${scope}`)}</option>
            {/each}
          </select>
        </div>
      </div>
      <p class="mt-3 text-xs text-text-muted" data-testid="ai-search-house-cost">
        {t(
          aiEnabled
            ? "settings.marketing.ai_search_cost_on"
            : "settings.marketing.ai_search_cost_off",
          { units: fmtNumber(perClient, 0) },
        )}
      </p>
    </fieldset>

    <!-- What each source is called, on every screen that names one (#446, widened): the
         marketing page, the client hub, the client's own homepage. The supplier behind the
         agency's service is not the client's business, so a keyed source (SE Ranking, Rank
         Math) is named for what it *measures* by default *in the portal* and the tenant may put
         their own product name on it everywhere — "Breik. Analytics" is one tenant's word and
         lives here, never in code (§2). Staff read the product name until they rename it. -->
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
        {#each LABELLED_CONNECTIONS as kind (kind)}
          <div>
            <label for={`portal-label-${kind}`} class="mb-1 block text-xs text-text-muted">
              {t(`marketing.connection.${kind}`)}
            </label>
            <input
              id={`portal-label-${kind}`}
              name={`portal_label_${kind}`}
              value={settings?.portal_source_labels?.[kind] ?? ""}
              placeholder={t(`marketing.connection.${kind}`)}
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

    <!-- The house channel grouping for the leads dashboard (docs/MARKETING.md). GA4's default
         channel groups regrouped into what a client can read; Cross-network (Performance Max)
         belongs under advertising, and "AI Assistant" keeps a group of its own. Anything not
         listed is "other". A client's profile may override the whole grouping. -->
    <fieldset class="border-t border-border pt-5">
      <legend class="mb-1 text-sm font-semibold text-text">
        {t("settings.marketing.channel_groups")}
      </legend>
      <p class="mb-3 text-xs text-text-muted">{t("settings.marketing.channel_groups_hint")}</p>
      <div class="grid gap-3 sm:grid-cols-3">
        {#each ["organic", "ads", "ai"] as group (group)}
          <div>
            <label for={`channel-group-${group}`} class="mb-1 block text-xs text-text-muted">
              {t(`marketing.leads.group.${group}`)}
            </label>
            <textarea
              id={`channel-group-${group}`}
              name={`channel_group_${group}`}
              rows="5"
              class={inputClass}
              value={(settings?.channel_groups?.[group] ?? []).join("\n")}></textarea>
          </div>
        {/each}
      </div>
    </fieldset>

    {#if form?.saved}
      <p class="text-sm text-green-600 dark:text-green-400">{t("settings.marketing.saved")}</p>
    {:else if form?.error}
      <p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
    {/if}

    <Button type="submit" loading={busy.is("")}>
      {t("common.save")}
    </Button>
  </form>
</section>
