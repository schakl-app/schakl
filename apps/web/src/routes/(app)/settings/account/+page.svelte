<script lang="ts">
  import { page } from "$app/state";
  import { enhance } from "$app/forms";
  import { localeLabel, t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import { InFlight } from "$lib/core/submit.svelte";
  import { THEME_MODES, themeModeLabel } from "$lib/core/theme-mode";
  import Avatar from "$lib/core/ui/Avatar.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import GoogleAccountCard from "$lib/modules/google/GoogleAccountCard.svelte";
  import NavPrefEditor from "$lib/core/ui/NavPrefEditor.svelte";
  import PasswordInput from "$lib/core/ui/PasswordInput.svelte";
  import { navItemsFor, resolveLabel, type NavLabelMap } from "$lib/core/registry";

  let { data, form } = $props();

  // "Copied" feedback for the one-time backup-codes reveal (the house clipboard pattern).
  let backupCopied = $state(false);
  const busy = new InFlight();

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

  // Personal sidebar layout (#169): the module items this person can see, in declared order (not
  // filtered by the personal pref — the editor must still offer hidden items to unhide). The row
  // text shows the org's custom label when set, resolved by key from the merged nav prefs.
  const navLabels = $derived(
    new Map<string, NavLabelMap>(
      ((page.data.navPref?.items ?? []) as { key: string; label?: NavLabelMap }[]).map((i) => [
        i.key,
        i.label,
      ]),
    ),
  );
  const navCandidates = $derived(
    navItemsFor(page.data.theme?.enabledModules ?? [], page.data.user).map((item) => ({
      key: item.key,
      label: resolveLabel(navLabels.get(item.key), item.label)(),
    })),
  );
</script>

<svelte:head>
  <title>{pageTitle(t("settings.account.title"))}</title>
</svelte:head>

<div class="mb-6">
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
          <form method="POST" action="?/saveAvatar" use:enhance={busy.wrap("avatarClear")}>
            <input type="hidden" name="clear" value="1" />
            <Button variant="danger-outline" size="sm" loading={busy.is("avatarClear")}>
              {t("settings.account.avatar_remove")}
            </Button>
          </form>
        {/if}
      </div>
    </div>
    <p class="mt-2 text-xs text-text-muted">{t("settings.account.avatar_help")}</p>
    {#if form?.avatarError}
      <p class="mt-1 text-sm text-red-600 dark:text-red-400">{t(form.avatarError)}</p>
    {/if}

    <form
      method="POST"
      action="?/updateProfile"
      use:enhance={busy.wrap(
        "profile",
        () =>
          ({ update }) =>
            update({ reset: false }),
      )}
      class="mt-4 space-y-4"
    >
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
        <p class="mt-1 text-xs text-text-muted">
          {data.localLogin
            ? t("settings.account.email_change_help")
            : t("settings.account.email_managed_by_sso")}
        </p>
      </div>
      {#if form?.saved}
        <p class="text-sm text-green-600 dark:text-green-400">{t("settings.account.saved")}</p>
      {/if}
      {#if form?.error}
        <p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
      {/if}
      <Button loading={busy.is("profile")}>
        {t("common.save")}
      </Button>
    </form>
  </section>

  <!-- Change email: the sign-in address moves only with the current password (account.py).
       Hidden when the org enforces SSO — the IdP owns the address then. -->
  {#if data.localLogin}
    <section class="rounded-xl border border-border bg-surface-raised p-5">
      <h2 class="text-sm font-semibold text-text">{t("settings.account.change_email")}</h2>
      <p class="mt-1 text-sm text-text-muted">{t("settings.account.email_change_help")}</p>
      <form
        method="POST"
        action="?/changeEmail"
        use:enhance={busy.wrap(
          "changeEmail",
          () =>
            ({ update }) =>
              update({ reset: true }),
        )}
        class="mt-4 space-y-4"
      >
        <div>
          <label for="new-email" class="mb-1 block text-sm font-medium text-text">
            {t("settings.account.new_email")}
          </label>
          <input
            id="new-email"
            name="email"
            type="email"
            autocomplete="email"
            required
            class="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand"
          />
        </div>
        <div>
          <label for="email-password" class="mb-1 block text-sm font-medium text-text">
            {t("settings.account.current_password")}
          </label>
          <PasswordInput id="email-password" name="password" required />
        </div>
        {#if form?.emailChanged}
          <p class="text-sm text-green-600 dark:text-green-400">
            {t("settings.account.email_changed")}
          </p>
        {/if}
        {#if form?.emailError}
          <p class="text-sm text-red-600 dark:text-red-400">{t(form.emailError)}</p>
        {/if}
        <Button loading={busy.is("changeEmail")}>
          {t("settings.account.change_email")}
        </Button>
      </form>
    </section>
  {/if}

  <!-- Change password (#161). Hidden when the org enforces SSO (no local password to change). -->
  {#if data.localLogin}
    <section class="rounded-xl border border-border bg-surface-raised p-5">
      <h2 class="text-sm font-semibold text-text">{t("settings.account.password")}</h2>
      <p class="mt-1 text-sm text-text-muted">{t("settings.account.password_help")}</p>
      <form
        method="POST"
        action="?/changePassword"
        use:enhance={busy.wrap(
          "changePassword",
          () =>
            ({ update }) =>
              update({ reset: true }),
        )}
        class="mt-4 space-y-4"
      >
        <div>
          <label for="new-password" class="mb-1 block text-sm font-medium text-text">
            {t("settings.account.new_password")}
          </label>
          <PasswordInput
            id="new-password"
            name="password"
            autocomplete="new-password"
            required
            minlength={8}
          />
        </div>
        <div>
          <label for="confirm-password" class="mb-1 block text-sm font-medium text-text">
            {t("settings.account.confirm_password")}
          </label>
          <PasswordInput
            id="confirm-password"
            name="password_confirm"
            autocomplete="new-password"
            required
            minlength={8}
          />
        </div>
        {#if form?.passwordChanged}
          <p class="text-sm text-green-600 dark:text-green-400">
            {t("settings.account.password_changed")}
          </p>
        {/if}
        {#if form?.passwordError}
          <p class="text-sm text-red-600 dark:text-red-400">{t(form.passwordError)}</p>
        {/if}
        <Button loading={busy.is("changePassword")}>
          {t("settings.account.change_password")}
        </Button>
      </form>
    </section>
  {/if}

  <!-- Two-factor authentication (docs/TWOFACTOR.md). Guards the local password login, so it
       shares its gate: on an SSO-enforced org the status call refused and the card is gone. -->
  {#if data.localLogin && data.twoFactor}
    <section class="rounded-xl border border-border bg-surface-raised p-5">
      <h2 class="text-sm font-semibold text-text">{t("settings.account.two_factor")}</h2>
      <p class="mt-1 text-sm text-text-muted">{t("settings.account.two_factor_help")}</p>

      {#if form?.twoFactorBackupCodes}
        <!-- Shown exactly once, right after confirm/regenerate — the API keeps only hashes. -->
        <div
          class="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-3 dark:border-amber-800 dark:bg-amber-950"
        >
          <p class="text-xs font-medium text-amber-800 dark:text-amber-200">
            {t("settings.account.two_factor_backup_title")}
          </p>
          <p class="mt-1 text-xs text-amber-800 dark:text-amber-200">
            {t("settings.account.two_factor_backup_once")}
          </p>
          <div
            class="mt-2 grid grid-cols-2 gap-1 font-mono text-sm text-amber-900 dark:text-amber-100 sm:grid-cols-5"
          >
            {#each form.twoFactorBackupCodes as code (code)}
              <span>{code}</span>
            {/each}
          </div>
          <button
            type="button"
            class="mt-2 rounded border border-amber-300 px-2 py-1 text-xs text-amber-800 hover:bg-amber-100 dark:border-amber-700 dark:text-amber-200 dark:hover:bg-amber-900"
            onclick={() => {
              navigator.clipboard.writeText((form?.twoFactorBackupCodes ?? []).join("\n"));
              backupCopied = true;
            }}
          >
            {backupCopied
              ? t("settings.account.two_factor_copied")
              : t("settings.account.two_factor_copy")}
          </button>
        </div>
      {/if}

      {#if form?.twoFactorSetup || form?.twoFactorConfirming}
        {#if form?.twoFactorSetup}
          <div class="mt-4 flex flex-col gap-4 sm:flex-row">
            <!-- Server-generated SVG on a white tile: scannable in dark mode too. -->
            <div class="h-fit w-fit shrink-0 rounded-lg bg-white p-2 [&_svg]:h-40 [&_svg]:w-40">
              {@html form.twoFactorSetup.qr_svg}
            </div>
            <div class="min-w-0 text-sm">
              <p class="text-text-muted">{t("settings.account.two_factor_scan")}</p>
              <p class="mt-2 text-xs font-medium text-text">
                {t("settings.account.two_factor_manual_key")}
              </p>
              <code class="break-all text-xs text-text-muted">{form.twoFactorSetup.secret}</code>
            </div>
          </div>
        {/if}
        <form
          method="POST"
          action="?/confirmTwoFactor"
          use:enhance={busy.wrap((input) =>
            input.submitter?.getAttribute("formaction") ? "cancel2fa" : "confirm2fa",
          )}
          class="mt-4 space-y-3"
        >
          <div>
            <label for="twofactor-code" class="mb-1 block text-sm font-medium text-text">
              {t("settings.account.two_factor_code")}
            </label>
            <input
              id="twofactor-code"
              name="code"
              type="text"
              inputmode="numeric"
              autocomplete="one-time-code"
              required
              class="w-40 rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand"
            />
          </div>
          {#if form?.twoFactorError}
            <p class="text-sm text-red-600 dark:text-red-400">{t(form.twoFactorError)}</p>
          {/if}
          <div class="flex gap-2">
            <Button loading={busy.is("confirm2fa")} disabled={busy.active}>
              {t("settings.account.two_factor_confirm")}
            </Button>
            <Button
              variant="secondary"
              type="submit"
              formaction="?/cancelTwoFactorSetup"
              formnovalidate
              loading={busy.is("cancel2fa")}
              disabled={busy.active}
            >
              {t("settings.account.two_factor_cancel_setup")}
            </Button>
          </div>
        </form>
      {:else if !data.twoFactor.enabled}
        {#if form?.twoFactorError}
          <p class="mt-3 text-sm text-red-600 dark:text-red-400">{t(form.twoFactorError)}</p>
        {/if}
        <form
          method="POST"
          action="?/setupTwoFactor"
          use:enhance={busy.wrap("setup2fa")}
          class="mt-4"
        >
          <p class="mb-3 text-sm text-text-muted">{t("settings.account.two_factor_off")}</p>
          <Button loading={busy.is("setup2fa")}>
            {t("settings.account.two_factor_enable")}
          </Button>
        </form>
      {:else}
        <p class="mt-3 text-sm text-green-600 dark:text-green-400">
          {t("settings.account.two_factor_on")}
        </p>
        <p class="mt-1 text-sm text-text-muted">
          {t("settings.account.two_factor_backup_remaining", {
            count: data.twoFactor.backup_codes_remaining,
          })}
        </p>

        {#if form?.twoFactorError}
          <p class="mt-3 text-sm text-red-600 dark:text-red-400">{t(form.twoFactorError)}</p>
        {/if}

        <!-- Fresh backup codes cost a current app code; turning off costs the password. -->
        <form
          method="POST"
          action="?/regenerateBackupCodes"
          use:enhance={busy.wrap(
            "regenCodes",
            () =>
              ({ update }) =>
                update({ reset: true }),
          )}
          class="mt-4 flex flex-wrap items-end gap-2"
        >
          <div>
            <label for="regen-code" class="mb-1 block text-xs text-text-muted">
              {t("settings.account.two_factor_backup_regenerate_help")}
            </label>
            <input
              id="regen-code"
              name="code"
              type="text"
              inputmode="numeric"
              autocomplete="one-time-code"
              required
              class="w-40 rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand"
            />
          </div>
          <Button variant="secondary" loading={busy.is("regenCodes")}>
            {t("settings.account.two_factor_backup_regenerate")}
          </Button>
        </form>

        <form
          method="POST"
          action="?/disableTwoFactor"
          use:enhance={busy.wrap(
            "disable2fa",
            () =>
              ({ update }) =>
                update({ reset: true }),
          )}
          class="mt-4 flex flex-wrap items-end gap-2 border-t border-border pt-4"
        >
          <div>
            <label for="disable-password" class="mb-1 block text-xs text-text-muted">
              {t("settings.account.two_factor_disable_help")}
            </label>
            <PasswordInput id="disable-password" name="password" required class="w-56" />
          </div>
          <Button variant="danger-outline" loading={busy.is("disable2fa")}>
            {t("settings.account.two_factor_disable")}
          </Button>
        </form>

        {#if data.twoFactor.sms_available}
          <div class="mt-4 border-t border-border pt-4">
            <h3 class="text-sm font-medium text-text">
              {t("settings.account.two_factor_sms")}
            </h3>
            {#if form?.twoFactorSmsError}
              <p class="mt-2 text-sm text-red-600 dark:text-red-400">
                {t(form.twoFactorSmsError)}
              </p>
            {/if}
            {#if data.twoFactor.sms?.confirmed && !form?.twoFactorSmsPending}
              <p class="mt-1 text-sm text-text-muted">
                {t("settings.account.two_factor_sms_enabled", {
                  phone: data.twoFactor.sms.phone_masked,
                })}
              </p>
              <form
                method="POST"
                action="?/disableTwoFactorSms"
                use:enhance={busy.wrap("smsDisable")}
                class="mt-2"
              >
                <Button variant="secondary" size="sm" loading={busy.is("smsDisable")}>
                  {t("settings.account.two_factor_sms_disable")}
                </Button>
              </form>
            {:else if form?.twoFactorSmsPending}
              <form
                method="POST"
                action="?/confirmTwoFactorSms"
                use:enhance={busy.wrap("smsConfirm")}
                class="mt-2 flex flex-wrap items-end gap-2"
              >
                <div>
                  <label for="sms-code" class="mb-1 block text-xs text-text-muted">
                    {t("settings.account.two_factor_sms_pending", {
                      phone: form.twoFactorSmsPending,
                    })}
                  </label>
                  <input
                    id="sms-code"
                    name="code"
                    type="text"
                    inputmode="numeric"
                    autocomplete="one-time-code"
                    required
                    class="w-40 rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand"
                  />
                </div>
                <Button loading={busy.is("smsConfirm")}>
                  {t("settings.account.two_factor_sms_confirm")}
                </Button>
              </form>
            {:else}
              <p class="mt-1 text-sm text-text-muted">
                {t("settings.account.two_factor_sms_help")}
              </p>
              <form
                method="POST"
                action="?/setupTwoFactorSms"
                use:enhance={busy.wrap("smsSetup")}
                class="mt-2 flex flex-wrap items-end gap-2"
              >
                <div>
                  <label for="sms-phone" class="mb-1 block text-xs text-text-muted">
                    {t("settings.account.two_factor_sms_phone")} —
                    {t("settings.account.two_factor_sms_phone_help")}
                  </label>
                  <input
                    id="sms-phone"
                    name="phone"
                    type="tel"
                    placeholder="+31612345678"
                    required
                    class="w-56 rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand"
                  />
                </div>
                <Button variant="secondary" loading={busy.is("smsSetup")}>
                  {t("settings.account.two_factor_sms_send")}
                </Button>
              </form>
            {/if}
          </div>
        {/if}
      {/if}
    </section>
  {/if}

  <!-- Google koppelen (#22): a per-user grant, so it lives on the person (docs/GOOGLE.md). -->
  {#if data.google}
    <GoogleAccountCard data={data.google} status={data.googleStatus} />
  {/if}

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
                  {key.expires_at
                    ? t("settings.account.api_key_expires", { date: key.expires_at.slice(0, 10) })
                    : t("settings.account.api_key_no_expiry")}
                </span>
              </div>
              {#if !key.revoked_at}
                <form
                  method="POST"
                  action="?/revokeKey"
                  use:enhance={busy.wrap(`revoke:${key.id}`)}
                >
                  <input type="hidden" name="key_id" value={key.id} />
                  <Button variant="danger-outline" size="xs" loading={busy.is(`revoke:${key.id}`)}>
                    {t("settings.account.api_key_revoke")}
                  </Button>
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
        use:enhance={busy.wrap("createKey", () => ({ result, update }) => {
          void update({ reset: result.type === "success" });
        })}
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
          <DateInput name="expires_at" id="key-expiry" />
          <p class="mt-1 text-xs text-text-muted">
            {t("settings.account.api_key_expiry_help")}
          </p>
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
        <Button loading={busy.is("createKey")}>
          {t("settings.account.api_key_create")}
        </Button>
      </form>
    </section>
  {/if}

  <!-- Personal sidebar layout (#169): my own order/visibility, never the org's (UX §6). -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="text-sm font-semibold text-text">{t("settings.account.nav_title")}</h2>
    <p class="mb-4 mt-1 text-sm text-text-muted">{t("settings.account.nav_subtitle")}</p>
    {#if form?.navSaved || form?.navReset}
      <p class="mb-3 text-sm text-green-700 dark:text-green-400">{t("settings.account.saved")}</p>
    {/if}
    {#key data.personalNavItems}
      <NavPrefEditor
        candidates={navCandidates}
        initial={data.personalNavItems}
        action="?/saveNav"
        resetAction="?/resetNav"
        showReset={data.hasPersonalNav}
      />
    {/key}
  </section>
</div>
