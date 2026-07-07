<script lang="ts">
  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";

  let { data, form } = $props();

  const brand = $derived(page.data.theme?.brandName || "");
</script>

<svelte:head>
  <title>{t("auth.sign_in")}{brand ? ` · ${brand}` : ""}</title>
</svelte:head>

<div class="flex min-h-screen items-center justify-center px-4">
  <div class="w-full max-w-sm rounded-2xl border border-neutral-200 bg-white p-8 shadow-sm">
    <div class="mb-6 text-center">
      {#if page.data.theme?.logoUrl}
        <img src={page.data.theme.logoUrl} alt={brand} class="mx-auto mb-3 h-10" />
      {/if}
      <h1 class="text-lg font-semibold text-neutral-900">
        {brand || t("auth.welcome")}
      </h1>
      <p class="mt-1 text-sm text-neutral-500">{t("auth.sign_in")}</p>
    </div>

    {#if data.localLoginEnabled}
      <form method="POST" use:enhance class="space-y-4">
        <div>
          <label for="email" class="mb-1 block text-sm font-medium text-neutral-700">
            {t("auth.email")}
          </label>
          <input
            id="email"
            name="email"
            type="email"
            required
            autocomplete="username"
            value={form?.email ?? ""}
            class="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
          />
        </div>
        <div>
          <label for="password" class="mb-1 block text-sm font-medium text-neutral-700">
            {t("auth.password")}
          </label>
          <input
            id="password"
            name="password"
            type="password"
            required
            autocomplete="current-password"
            class="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
          />
        </div>

        {#if form?.error}
          <p class="text-sm text-red-600">{t(form.error)}</p>
        {/if}

        <button
          type="submit"
          class="w-full rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white transition hover:opacity-90"
        >
          {t("auth.sign_in_action")}
        </button>
      </form>
    {:else}
      <p class="text-center text-sm text-neutral-600">{t("auth.local_login_disabled")}</p>
    {/if}

    {#if data.oidcEnabled}
      <a
        href="/api/v1/auth/oidc/login"
        class="mt-4 block w-full rounded-lg border border-neutral-300 px-4 py-2 text-center text-sm font-medium text-neutral-700 hover:bg-neutral-50"
      >
        {t("auth.sign_in_with_oidc")}
      </a>
    {/if}
  </div>
</div>
