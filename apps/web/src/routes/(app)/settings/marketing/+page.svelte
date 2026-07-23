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
  <form method="POST" action="?/save" use:enhance={busy.wrap()} class="space-y-5">
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

    <Button type="submit" loading={busy.active}>
      {t("common.save")}
    </Button>
  </form>
</section>
