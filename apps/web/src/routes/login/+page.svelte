<script lang="ts">
  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { announceSignedOut } from "$lib/core/session-watch";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import Button from "$lib/core/ui/Button.svelte";
  import PasswordInput from "$lib/core/ui/PasswordInput.svelte";

  let { data, form } = $props();

  const busy = new InFlight();

  /**
   * Tell the other tabs that this browser has no session (`SessionGuard`).
   *
   * Here rather than on the sign-out button, because *arriving on this page* is the proof:
   * `load` bounces anyone who still has a session, so reaching it means the cookie is already
   * gone. Announcing from the button instead announced an intention — the receiving tab's
   * confirming probe then raced the deletion, won, and stood the prompt back down half a
   * second after raising it. It also widens the signal for free: an expired session redirected
   * here, or a sign-out from a screen whose JS never ran, says the same thing.
   */
  $effect(() => {
    announceSignedOut();
  });

  const brand = $derived(page.data.theme?.brandName || "");

  // Which factor the challenge step is asking for; TOTP first, backup codes behind a link.
  let method = $state<"totp" | "backup">("totp");
  const challenge = $derived(form?.twoFactor ? form : null);
  const canSms = $derived(challenge?.methods?.includes("sms") ?? false);

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<svelte:head>
  <title>{pageTitle(t("auth.sign_in"))}</title>
</svelte:head>

<div class="flex min-h-screen items-center justify-center px-4">
  <div class="w-full max-w-sm rounded-2xl border border-border bg-surface-raised p-8 shadow-sm">
    <div class="mb-6 text-center">
      {#if page.data.theme?.logoUrl}
        <img src={page.data.theme.logoUrl} alt={brand} class="mx-auto mb-3 h-10" />
      {/if}
      <h1 class="text-lg font-semibold text-text">
        {brand || t("auth.welcome")}
      </h1>
      <p class="mt-1 text-sm text-text-muted">
        {challenge ? t("auth.two_factor_title") : t("auth.sign_in")}
      </p>
    </div>

    {#if !page.data.theme?.resolved}
      <!-- Unknown hostname: resolution is strict (issue #26) — say so instead of a dead form. -->
      <p class="text-center text-sm text-text-muted">{t("auth.unknown_host")}</p>
    {:else if page.data.theme?.suspended}
      <p class="text-center text-sm text-text-muted">{t("auth.org_suspended")}</p>
    {:else if challenge}
      <!-- Second step: the password checked out, a code from an enrolled factor finishes it. -->
      <form method="POST" action="?/verify" use:enhance={busy.wrap("verify")} class="space-y-4">
        <input type="hidden" name="challenge_token" value={challenge.challengeToken} />
        <input type="hidden" name="methods" value={challenge.methods.join(",")} />
        <input type="hidden" name="next" value={data.next ?? ""} />
        <input type="hidden" name="method" value={challenge.smsSentTo ? "sms" : method} />

        <p class="text-sm text-text-muted">
          {#if challenge.smsSentTo}
            {t("auth.two_factor_sms_sent", { phone: challenge.smsSentTo })}
          {:else if method === "backup"}
            {t("auth.two_factor_backup_hint")}
          {:else}
            {t("auth.two_factor_hint")}
          {/if}
        </p>

        <div>
          <label for="code" class="mb-1 block text-sm font-medium text-text">
            {method === "backup" && !challenge.smsSentTo
              ? t("auth.two_factor_backup_code")
              : t("auth.two_factor_code")}
          </label>
          <input
            id="code"
            name="code"
            type="text"
            required
            autocomplete="one-time-code"
            inputmode={method === "backup" ? "text" : "numeric"}
            class={inputClass}
          />
        </div>

        {#if form?.error}
          <p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
        {/if}

        <Button type="submit" class="w-full" loading={busy.is("verify")}>
          {t("auth.two_factor_verify")}
        </Button>

        <p class="space-y-1 text-center text-sm">
          <button
            type="button"
            class="block w-full text-text-muted hover:text-brand"
            onclick={() => (method = method === "totp" ? "backup" : "totp")}
          >
            {method === "totp" ? t("auth.two_factor_use_backup") : t("auth.two_factor_use_totp")}
          </button>
        </p>
      </form>

      {#if canSms && !challenge.smsSentTo}
        <form method="POST" action="?/sms" use:enhance class="mt-2 text-center">
          <input type="hidden" name="challenge_token" value={challenge.challengeToken} />
          <input type="hidden" name="methods" value={challenge.methods.join(",")} />
          <button type="submit" class="text-sm text-text-muted hover:text-brand">
            {t("auth.two_factor_send_sms")}
          </button>
        </form>
      {/if}
    {:else if data.localLoginEnabled}
      <form method="POST" action="?/login" use:enhance={busy.clear("login")} class="space-y-4">
        <!-- Where they were headed before the guard sent them here. On the form, not read back
             off the page URL: a form action posts to `?/login`, which carries no other query. -->
        <input type="hidden" name="next" value={data.next ?? ""} />
        <div>
          <label for="email" class="mb-1 block text-sm font-medium text-text">
            {t("auth.email")}
          </label>
          <input
            id="email"
            name="email"
            type="email"
            required
            autocomplete="username"
            value={form?.email ?? ""}
            class={inputClass}
          />
        </div>
        <div>
          <label for="password" class="mb-1 block text-sm font-medium text-text">
            {t("auth.password")}
          </label>
          <PasswordInput id="password" name="password" required />
        </div>

        {#if form?.error}
          <p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
        {/if}

        <Button type="submit" class="w-full" loading={busy.is("login")}>
          {t("auth.sign_in_action")}
        </Button>

        <p class="text-center">
          <a href="/forgot-password" class="text-sm text-text-muted hover:text-brand">
            {t("auth.forgot_password")}
          </a>
        </p>
      </form>
    {:else}
      <p class="text-center text-sm text-text-muted">{t("auth.local_login_disabled")}</p>
    {/if}

    <!-- Why an SSO attempt bounced back to this page. Outside the local-login branch on
         purpose: an org that *enforces* SSO renders no form at all, and that is exactly the
         org whose people arrive here after a refused federated sign-in. -->
    {#if data.error && !challenge}
      <p class="mt-4 text-center text-sm text-red-600 dark:text-red-400">{t(data.error)}</p>
    {/if}

    {#if data.oidcEnabled && !challenge}
      <a
        href="/api/v1/auth/oidc/login"
        class="mt-4 block w-full rounded-lg border border-border px-4 py-2 text-center text-sm font-medium text-text hover:bg-surface"
      >
        {t("auth.sign_in_with_sso", { name: data.oidcName || "SSO" })}
      </a>
    {/if}
  </div>
</div>
