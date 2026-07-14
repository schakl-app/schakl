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
  import { pageTitle } from "$lib/core/title";

  let { data, form } = $props();
  const settings = $derived(data.settings);

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<svelte:head>
  <title>{pageTitle(t("settings.marketing.title"))}</title>
</svelte:head>

<a href="/settings" class="text-sm text-text-muted hover:text-text">← {t("settings.title")}</a>
<h1 class="mb-1 mt-2 text-xl font-semibold text-text">{t("settings.marketing.title")}</h1>
<p class="mb-6 text-sm text-text-muted">{t("settings.marketing.subtitle")}</p>

<section class="max-w-2xl rounded-xl border border-border bg-surface-raised p-5">
  <form method="POST" action="?/save" use:enhance class="space-y-5">
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

    {#if form?.saved}
      <p class="text-sm text-green-600 dark:text-green-400">{t("settings.marketing.saved")}</p>
    {:else if form?.error}
      <p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
    {/if}

    <button
      type="submit"
      class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
    >
      {t("common.save")}
    </button>
  </form>
</section>
