<script lang="ts">
  import { page } from "$app/state";
  import { enhance } from "$app/forms";
  import { localeLabel, t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import { THEME_MODES, themeModeLabel } from "$lib/core/theme-mode";

  let { data, form } = $props();

  const account = $derived(data.account);
  const path = $derived(page.url.pathname);

  // A worked example so the choice is concrete (17 Jan 2026, 14:05). Built from the parts here
  // rather than through format.ts, so it reflects the saved choice on this reload without waiting
  // on the cookie/`<html>` seam.
  const sampleDate = $derived(
    data.currentFormat.date === "yyyy-mm-dd"
      ? "2026-01-17"
      : data.currentFormat.date === "mm-dd-yyyy"
        ? "01-17-2026"
        : "17-01-2026",
  );
  const sampleTime = $derived(data.currentFormat.clock === "12h" ? "2:05 PM" : "14:05");
</script>

<svelte:head>
  <title>{pageTitle(t("settings.account.title"))}</title>
</svelte:head>

<div class="mb-6">
  <a href="/settings" class="text-sm text-text-muted hover:text-text">← {t("settings.title")}</a>
  <h1 class="mt-1 text-xl font-semibold text-text">{t("settings.account.title")}</h1>
  <p class="mt-1 text-sm text-text-muted">{t("settings.account.subtitle")}</p>
</div>

<div class="max-w-2xl space-y-6">
  <!-- Language -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="text-sm font-semibold text-text">{t("settings.account.language")}</h2>
    <p class="mt-1 text-sm text-text-muted">{t("settings.account.language_help")}</p>
    <form method="POST" action="/set-locale" class="mt-4">
      <input type="hidden" name="redirect" value={path} />
      <select
        name="locale"
        onchange={(e) => e.currentTarget.form?.requestSubmit()}
        class="w-full max-w-xs rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand sm:w-auto"
      >
        {#each data.locales as loc (loc)}
          <option value={loc} selected={data.currentLocale === loc}>{localeLabel(loc)}</option>
        {/each}
      </select>
      <noscript>
        <button
          class="ml-2 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >
          {t("common.save")}
        </button>
      </noscript>
    </form>
  </section>

  <!-- Appearance -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="text-sm font-semibold text-text">{t("settings.account.appearance")}</h2>
    <p class="mt-1 text-sm text-text-muted">{t("settings.account.appearance_help")}</p>
    <form method="POST" action="/set-theme" class="mt-4">
      <input type="hidden" name="redirect" value={path} />
      <select
        name="theme"
        onchange={(e) => e.currentTarget.form?.requestSubmit()}
        class="w-full max-w-xs rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand sm:w-auto"
      >
        {#each THEME_MODES as mode (mode)}
          <option value={mode} selected={data.currentTheme === mode}>{themeModeLabel(mode)}</option>
        {/each}
      </select>
      <noscript>
        <button
          class="ml-2 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >
          {t("common.save")}
        </button>
      </noscript>
    </form>
  </section>

  <!-- Formatting (issue #13): personal date/time conventions, independent of the UI language. -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="text-sm font-semibold text-text">{t("settings.account.formatting")}</h2>
    <p class="mt-1 text-sm text-text-muted">{t("settings.account.formatting_help")}</p>
    <div class="mt-4 flex flex-col gap-4 sm:flex-row">
      <label class="flex-1">
        <span class="mb-1 block text-sm font-medium text-text">
          {t("settings.account.clock")}
        </span>
        <form method="POST" action="/set-format">
          <input type="hidden" name="redirect" value={path} />
          <select
            name="clock"
            onchange={(e) => e.currentTarget.form?.requestSubmit()}
            class="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand"
          >
            {#each data.clocks as c (c)}
              <option value={c} selected={data.currentFormat.clock === c}>
                {t(`settings.account.clock.${c}`)}
              </option>
            {/each}
          </select>
        </form>
      </label>
      <label class="flex-1">
        <span class="mb-1 block text-sm font-medium text-text">
          {t("settings.account.date_format")}
        </span>
        <form method="POST" action="/set-format">
          <input type="hidden" name="redirect" value={path} />
          <select
            name="date"
            onchange={(e) => e.currentTarget.form?.requestSubmit()}
            class="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand"
          >
            {#each data.dateFormats as d (d)}
              <option value={d} selected={data.currentFormat.date === d}>
                {t(`settings.account.date_format.${d}`)}
              </option>
            {/each}
          </select>
        </form>
      </label>
    </div>
    <p class="mt-3 text-xs text-text-muted">
      {t("settings.account.format_example", { date: sampleDate, time: sampleTime })}
    </p>
  </section>

  <!-- Profile -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="text-sm font-semibold text-text">{t("settings.account.profile")}</h2>
    <form method="POST" action="?/updateProfile" use:enhance class="mt-4 space-y-4">
      <div>
        <label for="full_name" class="mb-1 block text-sm font-medium text-text">
          {t("settings.account.full_name")}
        </label>
        <input
          id="full_name"
          name="full_name"
          value={account?.full_name ?? ""}
          class="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand"
        />
      </div>
      <div>
        <label for="email" class="mb-1 block text-sm font-medium text-text">
          {t("settings.account.email")}
        </label>
        <input
          id="email"
          value={account?.email ?? ""}
          disabled
          class="w-full cursor-not-allowed rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text-muted"
        />
        <p class="mt-1 text-xs text-text-muted">{t("settings.account.email_help")}</p>
      </div>
      <div>
        <span class="mb-1 block text-sm font-medium text-text">{t("settings.account.role")}</span>
        <span
          class="inline-block rounded-full bg-surface px-3 py-1 text-xs font-medium text-text-muted"
        >
          {t(`roles.${account?.role}`)}
        </span>
      </div>

      {#if form?.saved}
        <p class="text-sm text-green-600 dark:text-green-400">{t("settings.account.saved")}</p>
      {/if}
      {#if form?.error}
        <p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
      {/if}
      <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90">
        {t("common.save")}
      </button>
    </form>
  </section>
</div>
