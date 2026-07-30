<script lang="ts">
  import { enhance } from "$app/forms";
  import FormCheckbox from "$lib/core/ui/FormCheckbox.svelte";
  import { currencyLabel } from "$lib/core/currencies";
  import { phoneCountries } from "$lib/core/phone";
  import { localeLabel, t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle, renderTabTitle } from "$lib/core/title";
  import Button from "$lib/core/ui/Button.svelte";
  import ImageField from "$lib/core/ui/ImageField.svelte";
  import { getLocale } from "$lib/paraglide/runtime";

  let { data, form } = $props();

  const branding = $derived(data.branding);
  let primary = $state(data.branding?.primary_color ?? "#4f46e5");
  let accent = $state(data.branding?.accent_color ?? "#0ea5e9");
  // Live preview while typing (#97): the same renderer the real tab uses.
  let tabTemplate = $state(data.branding?.tab_title_template ?? "");
  const tabPreview = $derived(
    renderTabTitle(
      tabTemplate.trim() || "{brand} · {page}",
      t("settings.branding.tab_title_example"),
      data.branding?.brand_name ?? "",
    ),
  );
  const tabTemplateInvalid = $derived.by(() => {
    const template = tabTemplate.trim();
    if (!template) return false;
    const tokens = [...template.matchAll(/\{([^{}]*)\}/g)].map((m) => m[1]);
    return !tokens.includes("page") || tokens.some((tok) => tok !== "page" && tok !== "brand");
  });

  const busy = new InFlight();

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<svelte:head>
  <title>{pageTitle(t("settings.branding.title"))}</title>
</svelte:head>

<div class="mb-6">
  <h1 class="mt-2 text-xl font-semibold text-text">{t("settings.branding.title")}</h1>
  <p class="mt-1 text-sm text-text-muted">{t("settings.branding.subtitle")}</p>
</div>

{#if branding}
  <div class="grid gap-4 lg:grid-cols-[1fr_320px]">
    <!-- reset: false — the default form reset would snap the color pickers back to their
         server-rendered initial values right after saving. -->
    <form
      method="POST"
      action="?/update"
      enctype="multipart/form-data"
      use:enhance={busy.wrap("save", () => async ({ update }) => {
        await update({ reset: false });
      })}
      class="rounded-xl border border-border bg-surface-raised p-5"
    >
      <!-- Two subjects, one save. The card used to mix the brand with the org's clock, money and
           country in one eleven-control grid, so "waar stel ik de valuta in?" had no scent to
           follow; naming the halves costs nothing and answers it. Still one form and one save
           button (docs/UX.md: one save per editing surface, never per field). -->
      <h2 class="mb-3 text-sm font-semibold text-text">
        {t("settings.branding.identity_heading")}
      </h2>
      <div class="grid gap-3 sm:grid-cols-2">
        <div>
          <label for="brand_name" class="mb-1 block text-sm font-medium text-text"
            >{t("settings.branding.brand_name")}</label
          >
          <input
            id="brand_name"
            name="brand_name"
            value={branding.brand_name}
            required
            class={inputClass}
          />
          <label class="mt-2 flex items-center gap-2 text-sm text-text">
            <FormCheckbox
              name="show_brand_name"
              checked={branding.show_brand_name}
              class="h-4 w-4 rounded border-border text-brand focus:ring-brand"
            />
            {t("settings.branding.show_brand_name")}
          </label>
        </div>
        <div>
          <label for="tab_title_template" class="mb-1 block text-sm font-medium text-text"
            >{t("settings.branding.tab_title")}</label
          >
          <input
            id="tab_title_template"
            name="tab_title_template"
            bind:value={tabTemplate}
            defaultValue={data.branding?.tab_title_template ?? ""}
            placeholder={"{page} · {brand}"}
            class={inputClass}
          />
          <p class="mt-1 text-xs text-text-muted">
            {t("settings.branding.tab_title_help", { pageToken: "{page}", brandToken: "{brand}" })}
            {#if !tabTemplateInvalid}
              · {t("settings.branding.tab_title_preview", { preview: tabPreview })}
            {/if}
          </p>
          {#if tabTemplateInvalid}
            <p class="mt-1 text-xs text-red-600 dark:text-red-400">
              {t("settings.branding.tab_title_invalid", {
                pageToken: "{page}",
                brandToken: "{brand}",
              })}
            </p>
          {/if}
        </div>
        <ImageField
          id="logo_file"
          name="logo_url"
          fileName="logo_file"
          label={t("settings.branding.logo")}
          value={branding.logo_url}
          accept="image/png,image/jpeg,image/webp,image/gif,image/svg+xml"
        />
        <ImageField
          id="favicon_file"
          name="favicon_url"
          fileName="favicon_file"
          label={t("settings.branding.favicon")}
          value={branding.favicon_url}
          accept="image/png,image/svg+xml,image/x-icon,image/vnd.microsoft.icon"
        />
        <!-- The installable-app icon (#198): a different asset from the favicon — a square
             raster the PWA manifest and the iOS home-screen icon derive their sizes from. -->
        <ImageField
          id="app_icon_file"
          name="app_icon_url"
          fileName="app_icon_file"
          label={t("settings.branding.app_icon")}
          value={branding.app_icon_url}
          accept="image/png,image/jpeg,image/webp"
          help={t("settings.branding.app_icon_help")}
        />
        <div class="grid gap-3 sm:grid-cols-2">
          <div>
            <label for="primary_color" class="mb-1 block text-sm font-medium text-text"
              >{t("settings.branding.primary_color")}</label
            >
            <div class="flex items-center gap-2">
              <input
                id="primary_color"
                name="primary_color"
                type="color"
                bind:value={primary}
                defaultValue={data.branding?.primary_color ?? "#4f46e5"}
                class="h-9 w-12 cursor-pointer rounded border border-border"
              />
              <span class="font-mono text-sm text-text-muted">{primary}</span>
            </div>
          </div>
          <div>
            <label for="accent_color" class="mb-1 block text-sm font-medium text-text"
              >{t("settings.branding.accent_color")}</label
            >
            <div class="flex items-center gap-2">
              <input
                id="accent_color"
                name="accent_color"
                type="color"
                bind:value={accent}
                defaultValue={data.branding?.accent_color ?? "#0ea5e9"}
                class="h-9 w-12 cursor-pointer rounded border border-border"
              />
              <span class="font-mono text-sm text-text-muted">{accent}</span>
            </div>
          </div>
        </div>
      </div>

      <h2 class="mb-3 mt-6 border-t border-border pt-5 text-sm font-semibold text-text">
        {t("settings.branding.region_heading")}
      </h2>
      <div class="grid gap-3 sm:grid-cols-2">
        <div>
          <label for="default_locale" class="mb-1 block text-sm font-medium text-text"
            >{t("settings.branding.default_locale")}</label
          >
          <select id="default_locale" name="default_locale" class={inputClass}>
            {#each data.locales as locale (locale)}
              <option value={locale} selected={branding.default_locale === locale}
                >{localeLabel(locale)}</option
              >
            {/each}
          </select>
        </div>
        <div>
          <label for="timezone" class="mb-1 block text-sm font-medium text-text"
            >{t("settings.branding.timezone")}</label
          >
          <select id="timezone" name="timezone" class={inputClass}>
            <optgroup label={t("settings.branding.timezone_common")}>
              {#each data.commonTimezones as tz (tz)}
                <option value={tz} selected={branding.timezone === tz}>{tz}</option>
              {/each}
            </optgroup>
            <optgroup label={t("settings.branding.timezone_other")}>
              {#each data.otherTimezones as tz (tz)}
                <option value={tz} selected={branding.timezone === tz}>{tz}</option>
              {/each}
            </optgroup>
          </select>
          <p class="mt-1 text-xs text-text-muted">{t("settings.branding.timezone_help")}</p>
        </div>
        <div>
          <label for="default-country" class="mb-1 block text-sm font-medium text-text"
            >{t("settings.branding.default_country")}</label
          >
          <select id="default-country" name="default_country" class={inputClass}>
            {#each phoneCountries() as country (country.code)}
              <option value={country.code} selected={branding.default_country === country.code}
                >{country.name}</option
              >
            {/each}
          </select>
          <p class="mt-1 text-xs text-text-muted">{t("settings.branding.default_country_help")}</p>
        </div>
        <div>
          <label for="currency" class="mb-1 block text-sm font-medium text-text"
            >{t("settings.branding.currency")}</label
          >
          <select id="currency" name="currency" class={inputClass}>
            <optgroup label={t("settings.branding.timezone_common")}>
              {#each data.commonCurrencies as code (code)}
                <option value={code} selected={branding.currency === code}
                  >{currencyLabel(code, getLocale())}</option
                >
              {/each}
            </optgroup>
            <optgroup label={t("settings.branding.currency_other")}>
              {#each data.otherCurrencies as code (code)}
                <option value={code} selected={branding.currency === code}
                  >{currencyLabel(code, getLocale())}</option
                >
              {/each}
            </optgroup>
          </select>
          <p class="mt-1 text-xs text-text-muted">{t("settings.branding.currency_help")}</p>
        </div>
      </div>
      <p class="mt-3 text-xs text-text-muted">{t("settings.branding.hint")}</p>
      {#if form?.error}<p class="mt-2 text-sm text-red-600 dark:text-red-400">
          {t(form.error)}
        </p>{/if}
      {#if form?.updated}<p class="mt-2 text-sm text-green-600 dark:text-green-400">
          {t("settings.account.saved")}
        </p>{/if}
      <Button class="mt-4" loading={busy.is("save")} disabled={busy.active}>
        {t("common.save")}
      </Button>
    </form>

    <!-- Live preview -->
    <aside
      class="h-fit rounded-xl border border-border bg-surface-raised p-5"
      style="--brand-primary: {primary}; --brand-accent: {accent};"
    >
      <h2 class="mb-3 text-xs font-semibold uppercase tracking-wide text-text-muted">
        {t("settings.branding.preview")}
      </h2>
      <div class="rounded-lg border border-border p-4">
        <div class="mb-3 flex items-center gap-2">
          {#if branding.logo_url}
            <img src={branding.logo_url} alt="" class="h-6 w-auto" />
          {/if}
          <span class="font-semibold text-text">{branding.brand_name}</span>
        </div>
        <button type="button" class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white"
          >{t("common.save")}</button
        >
        <span class="ml-2 rounded-full bg-brand/10 px-2 py-0.5 text-[11px] font-medium text-brand"
          >{t("time.today_badge")}</span
        >
      </div>
    </aside>
  </div>

  <!-- Custom domain (#292): the guided wizard owns claim → ownership → DNS → activation. -->
  <section
    class="mt-4 flex max-w-2xl flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-surface-raised p-5"
  >
    <div>
      <h2 class="text-sm font-semibold text-text">{t("settings.branding.domain.title")}</h2>
      <p class="mt-1 text-xs text-text-muted">{t("settings.branding.domain.subtitle")}</p>
      {#if data.domain?.custom_domain || data.domain?.pending_domain}
        <p class="mt-2 font-mono text-sm text-text">
          {data.domain.pending_domain ?? data.domain.custom_domain}
        </p>
      {/if}
      <p
        class="mt-1 text-xs {data.domain?.stage === 'active'
          ? 'text-green-600 dark:text-green-400'
          : 'text-text-muted'}"
      >
        {t(`settings.domain.stage.${data.domain?.stage ?? "none"}`)}
      </p>
    </div>
    <a
      href="/settings/domain"
      class="rounded-lg border border-border px-3 py-2 text-sm font-medium text-text hover:bg-surface"
    >
      {data.domain?.stage === "none"
        ? t("settings.domain.manage")
        : t("settings.domain.manage_existing")}
    </a>
  </section>
{/if}
