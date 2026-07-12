<script lang="ts">
  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";

  let { form } = $props();

  const brand = $derived(page.data.theme?.brandName || "");
</script>

<svelte:head>
  <title>{pageTitle(t("auth.forgot_title"))}</title>
</svelte:head>

<div class="flex min-h-screen items-center justify-center px-4">
  <div class="w-full max-w-sm rounded-2xl border border-border bg-surface-raised p-8 shadow-sm">
    <div class="mb-6 text-center">
      {#if page.data.theme?.logoUrl}
        <img src={page.data.theme.logoUrl} alt={brand} class="mx-auto mb-3 h-10" />
      {/if}
      <h1 class="text-lg font-semibold text-text">{t("auth.forgot_title")}</h1>
      <p class="mt-1 text-sm text-text-muted">{t("auth.forgot_hint")}</p>
    </div>

    {#if form?.sent}
      <!-- The same sentence whether the address exists or not — no user enumeration. -->
      <p class="text-center text-sm text-text">{t("auth.forgot_sent")}</p>
      <p class="mt-4 text-center">
        <a href="/login" class="text-sm text-text-muted hover:text-brand">
          {t("auth.back_to_login")}
        </a>
      </p>
    {:else}
      <form method="POST" use:enhance class="space-y-4">
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
            class="w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
          />
        </div>
        {#if form?.error}
          <p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
        {/if}
        <button
          type="submit"
          class="w-full rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white transition hover:opacity-90"
        >
          {t("auth.forgot_action")}
        </button>
        <p class="text-center">
          <a href="/login" class="text-sm text-text-muted hover:text-brand">
            {t("auth.back_to_login")}
          </a>
        </p>
      </form>
    {/if}
  </div>
</div>
