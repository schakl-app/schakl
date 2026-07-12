<script lang="ts">
  import { page } from "$app/state";
  import { enhance } from "$app/forms";
  import { localeLabel, t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import { THEME_MODES, themeModeLabel } from "$lib/core/theme-mode";
  import Avatar from "$lib/core/ui/Avatar.svelte";

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

    <!-- Profile picture (#122): OIDC by default, personally overridable, initials fallback. -->
    <div class="mt-4 flex items-center gap-4">
      <Avatar
        name={account?.full_name}
        email={account?.email}
        avatarUrl={account?.avatarUrl ?? null}
        size="md"
      />
      <div class="flex flex-wrap items-center gap-2">
        <form method="POST" action="?/saveAvatar" enctype="multipart/form-data" use:enhance>
          <label
            class="inline-flex cursor-pointer items-center rounded-lg border border-border px-3 py-1.5 text-sm text-text hover:border-brand"
          >
            {t("settings.account.avatar_upload")}
            <input
              type="file"
              name="file"
              accept="image/png,image/jpeg,image/webp,image/gif"
              class="hidden"
              onchange={(e) => e.currentTarget.form?.requestSubmit()}
            />
          </label>
        </form>
        {#if account?.customAvatarUrl}
          <form method="POST" action="?/saveAvatar" use:enhance>
            <input type="hidden" name="clear" value="1" />
            <button
              class="rounded-lg border border-border px-3 py-1.5 text-sm text-text-muted hover:text-red-600 dark:hover:text-red-400"
            >
              {t("settings.account.avatar_remove")}
            </button>
          </form>
        {/if}
      </div>
    </div>
    <p class="mt-2 text-xs text-text-muted">{t("settings.account.avatar_help")}</p>
    {#if form?.avatarError}
      <p class="mt-1 text-sm text-red-600 dark:text-red-400">{t(form.avatarError)}</p>
    {/if}

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

  <!-- Personal API keys (#20). Scoped, expiring, capped by what this member holds. -->
  {#if data.canManageKeys}
    <section class="rounded-xl border border-border bg-surface-raised p-6">
      <h2 class="mb-1 text-sm font-semibold text-text">{t("settings.account.api_keys")}</h2>
      <p class="mb-4 text-sm text-text-muted">{t("settings.account.api_keys_hint")}</p>

      {#if form?.createdSecret}
        <div
          class="mb-4 rounded-lg border border-amber-300 bg-amber-50 p-3 dark:border-amber-800 dark:bg-amber-950"
        >
          <p class="text-xs font-medium text-amber-800 dark:text-amber-200">
            {t("settings.account.api_key_created", { name: form.createdName ?? "" })}
          </p>
          <code
            class="mt-2 block overflow-x-auto rounded bg-surface px-2 py-1 font-mono text-xs text-text"
            >{form.createdSecret}</code
          >
          <p class="mt-1 text-xs text-amber-700 dark:text-amber-300">
            {t("settings.account.api_key_once")}
          </p>
        </div>
      {/if}

      {#if data.apiKeys.length > 0}
        <ul class="mb-4 divide-y divide-border rounded-lg border border-border">
          {#each data.apiKeys as key (key.id)}
            <li class="flex items-center gap-3 px-3 py-2 text-sm">
              <div class="min-w-0 flex-1">
                <span class="font-medium text-text">{key.name}</span>
                {#if key.revoked_at}
                  <span
                    class="ml-2 rounded-full bg-red-100 px-2 py-0.5 text-[11px] text-red-700 dark:bg-red-950 dark:text-red-300"
                    >{t("settings.account.api_key_revoked")}</span
                  >
                {/if}
                <span class="block truncate font-mono text-xs text-text-muted">{key.redacted}</span>
                <span class="block text-xs text-text-muted">
                  {t("settings.account.api_key_scopes", { count: key.scopes.length })} ·
                  {t("settings.account.api_key_expires", { date: key.expires_at.slice(0, 10) })}
                </span>
              </div>
              {#if !key.revoked_at}
                <form method="POST" action="?/revokeKey" use:enhance>
                  <input type="hidden" name="key_id" value={key.id} />
                  <button
                    class="rounded-lg border border-border px-2.5 py-1.5 text-xs text-text-muted hover:text-red-600 dark:hover:text-red-400"
                  >
                    {t("settings.account.api_key_revoke")}
                  </button>
                </form>
              {/if}
            </li>
          {/each}
        </ul>
      {/if}

      <form
        method="POST"
        action="?/createKey"
        class="space-y-3"
        use:enhance={() =>
          ({ result, update }) => {
            void update({ reset: result.type === "success" });
          }}
      >
        <div>
          <label for="key-name" class="mb-1 block text-sm font-medium text-text"
            >{t("settings.account.api_key_name")}</label
          >
          <input
            id="key-name"
            name="name"
            required
            placeholder={t("settings.account.api_key_name_placeholder")}
            class="w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand"
          />
        </div>
        <div>
          <label for="key-expiry" class="mb-1 block text-sm font-medium text-text"
            >{t("settings.account.api_key_expiry")}</label
          >
          <input
            id="key-expiry"
            name="expires_at"
            type="date"
            required
            class="rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand"
          />
        </div>
        <div>
          <span class="mb-1 block text-sm font-medium text-text"
            >{t("settings.account.api_key_scopes_label")}</span
          >
          <div class="max-h-48 space-y-1 overflow-y-auto rounded-lg border border-border p-2">
            {#each data.scopeOptions as scope (scope.value)}
              <label class="flex items-center gap-2 text-xs text-text">
                <input
                  type="checkbox"
                  name="scopes"
                  value={scope.value}
                  class="h-3.5 w-3.5 rounded border-border"
                />
                <span>{t(scope.label_key)}</span>
                {#if scope.value.includes(":")}
                  <span class="text-text-muted/70">({scope.value.split(":")[1]})</span>
                {/if}
              </label>
            {/each}
          </div>
        </div>
        {#if form?.error}<p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>{/if}
        <button
          class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >
          {t("settings.account.api_key_create")}
        </button>
      </form>
    </section>
  {/if}
</div>
