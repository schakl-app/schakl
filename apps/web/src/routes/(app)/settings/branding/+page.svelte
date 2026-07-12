<script lang="ts">
  import { enhance } from "$app/forms";
  import { currencyLabel } from "$lib/core/currencies";
  import { localeLabel, t } from "$lib/core/i18n";
  import { pageTitle, renderTabTitle } from "$lib/core/title";
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

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<svelte:head>
  <title>{pageTitle(t("settings.branding.title"))}</title>
</svelte:head>

<div class="mb-6">
  <a href="/settings" class="text-sm text-text-muted hover:text-text">← {t("settings.title")}</a>
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
      use:enhance={() =>
        async ({ update }) => {
          await update({ reset: false });
        }}
      class="rounded-xl border border-border bg-surface-raised p-5"
    >
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
            <input
              type="checkbox"
              name="show_brand_name"
              checked={branding.show_brand_name}
              class="h-4 w-4 rounded border-border text-brand focus:ring-brand"
            />
            {t("settings.branding.show_brand_name")}
          </label>
        </div>
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
        <div class="sm:col-span-2">
          <label for="tab_title_template" class="mb-1 block text-sm font-medium text-text"
            >{t("settings.branding.tab_title")}</label
          >
          <input
            id="tab_title_template"
            name="tab_title_template"
            bind:value={tabTemplate}
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
        <div>
          <label for="logo_file" class="mb-1 block text-sm font-medium text-text"
            >{t("settings.branding.logo")}</label
          >
          <input
            id="logo_file"
            name="logo_file"
            type="file"
            accept="image/png,image/jpeg,image/webp,image/gif,image/svg+xml"
            class="block w-full text-sm text-text-muted file:mr-3 file:cursor-pointer file:rounded-lg file:border file:border-solid file:border-border file:bg-transparent file:px-3 file:py-1.5 file:text-sm file:text-text hover:file:border-brand"
          />
          <label for="logo_url" class="mb-1 mt-2 block text-xs text-text-muted"
            >{t("settings.branding.logo_url")}</label
          >
          <input
            id="logo_url"
            name="logo_url"
            value={branding.logo_url ?? ""}
            placeholder="https://…"
            class={inputClass}
          />
          <p class="mt-1 text-xs text-text-muted">{t("settings.branding.upload_help")}</p>
        </div>
        <div>
          <label for="favicon_file" class="mb-1 block text-sm font-medium text-text"
            >{t("settings.branding.favicon")}</label
          >
          <input
            id="favicon_file"
            name="favicon_file"
            type="file"
            accept="image/png,image/svg+xml,image/x-icon,image/vnd.microsoft.icon"
            class="block w-full text-sm text-text-muted file:mr-3 file:cursor-pointer file:rounded-lg file:border file:border-solid file:border-border file:bg-transparent file:px-3 file:py-1.5 file:text-sm file:text-text hover:file:border-brand"
          />
          <label for="favicon_url" class="mb-1 mt-2 block text-xs text-text-muted"
            >{t("settings.branding.favicon_url")}</label
          >
          <input
            id="favicon_url"
            name="favicon_url"
            value={branding.favicon_url ?? ""}
            placeholder="https://…"
            class={inputClass}
          />
          <p class="mt-1 text-xs text-text-muted">{t("settings.branding.upload_help")}</p>
        </div>
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
              class="h-9 w-12 cursor-pointer rounded border border-border"
            />
            <span class="font-mono text-sm text-text-muted">{accent}</span>
          </div>
        </div>
      </div>
      <p class="mt-3 text-xs text-text-muted">{t("settings.branding.hint")}</p>
      {#if form?.error}<p class="mt-2 text-sm text-red-600 dark:text-red-400">
          {t(form.error)}
        </p>{/if}
      {#if form?.updated}<p class="mt-2 text-sm text-green-600 dark:text-green-400">
          {t("settings.account.saved")}
        </p>{/if}
      <button
        class="mt-4 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
      >
        {t("common.save")}
      </button>
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

  <!-- Custom domain (issue #26): claimed here, proven via DNS TXT, only then routed. -->
  <section class="mt-4 max-w-2xl rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="text-sm font-semibold text-text">{t("settings.branding.domain.title")}</h2>
    <p class="mt-1 text-xs text-text-muted">{t("settings.branding.domain.subtitle")}</p>

    {#if data.domain?.custom_domain}
      <div class="mt-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <p class="font-mono text-sm text-text">{data.domain.custom_domain}</p>
          <p class="mt-0.5 text-xs text-green-600 dark:text-green-400">
            {t("settings.branding.domain.verified")}
          </p>
        </div>
        <form method="POST" action="?/clearDomain" use:enhance>
          <button
            class="rounded-lg border border-border px-3 py-1.5 text-sm text-text hover:bg-surface"
          >
            {t("settings.branding.domain.remove")}
          </button>
        </form>
      </div>
    {/if}

    {#if data.domain?.pending_domain}
      <div
        class="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-4 dark:border-amber-800 dark:bg-amber-950/30"
      >
        <p class="text-sm font-medium text-text">
          {t("settings.branding.domain.pending", { domain: data.domain.pending_domain })}
        </p>
        <p class="mt-2 text-xs text-text-muted">
          {t("settings.branding.domain.txt_instructions")}
        </p>
        <dl class="mt-2 space-y-1 font-mono text-xs text-text">
          <div class="flex gap-2">
            <dt class="shrink-0 text-text-muted">TXT</dt>
            <dd class="break-all">{data.domain.txt_record_name}</dd>
          </div>
          <div class="flex gap-2">
            <dt class="shrink-0 text-text-muted">→</dt>
            <dd class="break-all">{data.domain.txt_record_value}</dd>
          </div>
        </dl>
        <div class="mt-3 flex gap-2">
          <form method="POST" action="?/verifyDomain" use:enhance>
            <button
              class="rounded-lg bg-brand px-3 py-1.5 text-sm font-medium text-white hover:opacity-90"
            >
              {t("settings.branding.domain.verify")}
            </button>
          </form>
          <form method="POST" action="?/clearDomain" use:enhance>
            <button
              class="rounded-lg border border-border px-3 py-1.5 text-sm text-text hover:bg-surface"
            >
              {t("common.cancel")}
            </button>
          </form>
        </div>
      </div>
    {:else}
      <form
        method="POST"
        action="?/claimDomain"
        use:enhance
        class="mt-4 flex flex-wrap items-end gap-3"
      >
        <div class="grow">
          <label for="domain" class="mb-1 block text-sm font-medium text-text">
            {t("settings.branding.domain.claim_label")}
          </label>
          <input
            id="domain"
            name="domain"
            placeholder={t("settings.branding.domain.placeholder")}
            class="{inputClass} font-mono"
          />
        </div>
        <button
          class="rounded-lg border border-border px-4 py-2 text-sm font-medium text-text hover:bg-surface"
        >
          {t("settings.branding.domain.claim")}
        </button>
      </form>
    {/if}

    {#if form?.error && form?.domainError}
      <p class="mt-2 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
    {/if}
    {#if form?.domainVerified}
      <p class="mt-2 text-sm text-green-600 dark:text-green-400">
        {t("settings.branding.domain.verified_now")}
      </p>
    {/if}
  </section>
{/if}
