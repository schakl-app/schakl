<script lang="ts">
  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import Button from "$lib/core/ui/Button.svelte";
  import PasswordInput from "$lib/core/ui/PasswordInput.svelte";

  let { data, form } = $props();

  const busy = new InFlight();

  const brand = $derived(page.data.theme?.brandName || "");
</script>

<svelte:head>
  <title>{pageTitle(t("auth.reset_title"))}</title>
</svelte:head>

<div class="flex min-h-screen items-center justify-center px-4">
  <div class="w-full max-w-sm rounded-2xl border border-border bg-surface-raised p-8 shadow-sm">
    <div class="mb-6 text-center">
      {#if page.data.theme?.logoUrl}
        <img src={page.data.theme.logoUrl} alt={brand} class="mx-auto mb-3 h-10" />
      {/if}
      <h1 class="text-lg font-semibold text-text">{t("auth.reset_title")}</h1>
    </div>

    {#if form?.done}
      <p class="text-center text-sm text-text">{t("auth.reset_success")}</p>
      <p class="mt-4 text-center">
        <a href="/login" class="text-sm font-medium text-brand hover:underline">
          {t("auth.sign_in_action")}
        </a>
      </p>
    {:else if !data.token}
      <p class="text-center text-sm text-text-muted">{t("errors.reset_token_invalid")}</p>
      <p class="mt-4 text-center">
        <a href="/forgot-password" class="text-sm text-text-muted hover:text-brand">
          {t("auth.forgot_title")}
        </a>
      </p>
    {:else}
      <form method="POST" use:enhance={busy.wrap()} class="space-y-4">
        <input type="hidden" name="token" value={data.token} />
        <div>
          <label for="password" class="mb-1 block text-sm font-medium text-text">
            {t("auth.new_password")}
          </label>
          <PasswordInput
            id="password"
            name="password"
            required
            minlength={8}
            autocomplete="new-password"
          />
          <p class="mt-1 text-xs text-text-muted">{t("auth.password_rules")}</p>
        </div>
        <div>
          <label for="confirm" class="mb-1 block text-sm font-medium text-text">
            {t("auth.confirm_password")}
          </label>
          <PasswordInput
            id="confirm"
            name="confirm"
            required
            minlength={8}
            autocomplete="new-password"
          />
        </div>
        {#if form?.error}
          <p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
        {/if}
        <Button type="submit" class="w-full" loading={busy.active}>
          {t("auth.reset_action")}
        </Button>
      </form>
    {/if}
  </div>
</div>
