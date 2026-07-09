<script lang="ts">
  import { enhance } from "$app/forms";
  import { localeLabel, t } from "$lib/core/i18n";

  let { data, form } = $props();

  const branding = $derived(data.branding);
  let primary = $state(data.branding?.primary_color ?? "#4f46e5");
  let accent = $state(data.branding?.accent_color ?? "#0ea5e9");

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<svelte:head>
  <title>{t("settings.branding.title")}</title>
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
          <label for="logo_url" class="mb-1 block text-sm font-medium text-text"
            >{t("settings.branding.logo_url")}</label
          >
          <input
            id="logo_url"
            name="logo_url"
            value={branding.logo_url ?? ""}
            placeholder="https://…"
            class={inputClass}
          />
        </div>
        <div>
          <label for="favicon_url" class="mb-1 block text-sm font-medium text-text"
            >{t("settings.branding.favicon_url")}</label
          >
          <input
            id="favicon_url"
            name="favicon_url"
            value={branding.favicon_url ?? ""}
            placeholder="https://…"
            class={inputClass}
          />
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
{/if}
