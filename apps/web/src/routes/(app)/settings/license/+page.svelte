<script lang="ts">
  import { enhance } from "$app/forms";
  import { fmtDayMonthYear } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { moduleLabel } from "$lib/core/registry";
  import { pageTitle } from "$lib/core/title";

  let { data, form } = $props();

  const license = $derived(form?.license ?? data.license);

  const stateKey = (notice: string | null | undefined) =>
    notice === "grace"
      ? "settings.license.state_grace"
      : notice === "expired"
        ? "settings.license.state_expired"
        : notice === "unlicensed"
          ? "settings.license.state_unlicensed"
          : "settings.license.state_ok";
</script>

<svelte:head>
  <title>{pageTitle(t("settings.license.title"))}</title>
</svelte:head>

<div class="mb-6">
  <a href="/settings" class="text-sm text-text-muted hover:text-text">← {t("settings.title")}</a>
  <h1 class="mt-2 text-xl font-semibold text-text">{t("settings.license.title")}</h1>
  <p class="mt-1 max-w-lg text-sm text-text-muted">{t("settings.license.subtitle")}</p>
</div>

<div class="max-w-lg space-y-4">
  <div class="rounded-xl border border-border bg-surface-raised p-5">
    {#if license?.installed}
      <dl class="space-y-2 text-sm">
        <div class="flex justify-between gap-4">
          <dt class="text-text-muted">{t("settings.license.customer")}</dt>
          <dd class="font-medium text-text">{license.customer}</dd>
        </div>
        <div class="flex justify-between gap-4">
          <dt class="text-text-muted">{t("settings.license.plan")}</dt>
          <dd class="font-medium text-text">{license.plan}</dd>
        </div>
        <div class="flex justify-between gap-4">
          <dt class="text-text-muted">{t("settings.license.expires")}</dt>
          <dd class="font-medium text-text">
            {license.expires_at ? fmtDayMonthYear(license.expires_at) : "—"}
          </dd>
        </div>
        {#if license.grace_until}
          <div class="flex justify-between gap-4">
            <dt class="text-text-muted">{t("settings.license.grace_until")}</dt>
            <dd class="text-text">{fmtDayMonthYear(license.grace_until)}</dd>
          </div>
        {/if}
      </dl>
    {:else}
      <p class="text-sm text-text-muted">{t("settings.license.none")}</p>
      {#if license?.bootstrap_grace_until}
        <p class="mt-2 text-sm text-text-muted">
          {t("settings.license.bootstrap_until")}
          <span class="font-medium text-text"
            >{fmtDayMonthYear(license.bootstrap_grace_until)}</span
          >.
        </p>
      {/if}
    {/if}

    {#if license?.licensed?.length}
      <h3 class="mt-4 text-xs font-semibold uppercase tracking-wide text-text-muted">
        {t("settings.license.modules")}
      </h3>
      <ul class="mt-2 space-y-1.5">
        {#each license.licensed as item (item.sku)}
          <li class="flex items-center justify-between gap-4 text-sm">
            <span class="text-text">{moduleLabel(item.sku)}</span>
            <span
              class="rounded-full px-2 py-0.5 text-[11px] {item.entitled
                ? 'bg-green-500/10 text-green-700 dark:text-green-400'
                : item.writable
                  ? 'bg-amber-500/10 text-amber-700 dark:text-amber-400'
                  : 'bg-red-500/10 text-red-700 dark:text-red-400'}"
            >
              {t(stateKey(item.notice))}
            </span>
          </li>
        {/each}
      </ul>
    {/if}
  </div>

  <form
    method="POST"
    action="?/install"
    use:enhance
    class="rounded-xl border border-border bg-surface-raised p-5"
  >
    <label class="block text-sm font-medium text-text" for="license-key">
      {t("settings.license.key_label")}
    </label>
    <textarea
      id="license-key"
      name="key"
      rows="4"
      required
      placeholder={t("settings.license.key_placeholder")}
      class="mt-2 w-full min-w-0 rounded-lg border border-border bg-surface px-3 py-2 font-mono text-xs text-text focus:border-brand focus:ring-brand"
    ></textarea>
    {#if form?.error}<p class="mt-2 text-sm text-red-600">{t(form.error)}</p>{/if}
    {#if form?.installed}
      <p class="mt-2 text-sm text-green-600">{t("settings.license.installed")}</p>
    {/if}
    <button
      class="mt-3 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
    >
      {t("settings.license.install")}
    </button>
  </form>
</div>
