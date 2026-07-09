<script lang="ts">
  import { page } from "$app/state";
  import { enhance } from "$app/forms";
  import { localeLabel, t } from "$lib/core/i18n";

  let { data, form } = $props();

  const account = $derived(data.account);
  const path = $derived(page.url.pathname);
</script>

<svelte:head>
  <title>{t("settings.account.title")}</title>
</svelte:head>

<div class="mb-6">
  <a href="/settings" class="text-sm text-neutral-500 hover:text-neutral-900"
    >← {t("settings.title")}</a
  >
  <h1 class="mt-1 text-xl font-semibold text-neutral-900">{t("settings.account.title")}</h1>
  <p class="mt-1 text-sm text-neutral-500">{t("settings.account.subtitle")}</p>
</div>

<div class="max-w-2xl space-y-6">
  <!-- Language -->
  <section class="rounded-xl border border-neutral-200 bg-white p-5">
    <h2 class="text-sm font-semibold text-neutral-900">{t("settings.account.language")}</h2>
    <p class="mt-1 text-sm text-neutral-500">{t("settings.account.language_help")}</p>
    <form method="POST" action="/set-locale" class="mt-4">
      <input type="hidden" name="redirect" value={path} />
      <select
        name="locale"
        onchange={(e) => e.currentTarget.form?.requestSubmit()}
        class="w-full max-w-xs rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand sm:w-auto"
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

  <!-- Profile -->
  <section class="rounded-xl border border-neutral-200 bg-white p-5">
    <h2 class="text-sm font-semibold text-neutral-900">{t("settings.account.profile")}</h2>
    <form method="POST" action="?/updateProfile" use:enhance class="mt-4 space-y-4">
      <div>
        <label for="full_name" class="mb-1 block text-sm font-medium text-neutral-700">
          {t("settings.account.full_name")}
        </label>
        <input
          id="full_name"
          name="full_name"
          value={account?.full_name ?? ""}
          class="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
        />
      </div>
      <div>
        <label for="email" class="mb-1 block text-sm font-medium text-neutral-700">
          {t("settings.account.email")}
        </label>
        <input
          id="email"
          value={account?.email ?? ""}
          disabled
          class="w-full cursor-not-allowed rounded-lg border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm text-neutral-500"
        />
        <p class="mt-1 text-xs text-neutral-400">{t("settings.account.email_help")}</p>
      </div>
      <div>
        <span class="mb-1 block text-sm font-medium text-neutral-700"
          >{t("settings.account.role")}</span
        >
        <span
          class="inline-block rounded-full bg-neutral-100 px-3 py-1 text-xs font-medium text-neutral-600"
        >
          {t(`roles.${account?.role}`)}
        </span>
      </div>

      {#if form?.saved}
        <p class="text-sm text-green-600">{t("settings.account.saved")}</p>
      {/if}
      {#if form?.error}
        <p class="text-sm text-red-600">{t(form.error)}</p>
      {/if}
      <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90">
        {t("common.save")}
      </button>
    </form>
  </section>
</div>
