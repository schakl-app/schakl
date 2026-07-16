<script lang="ts">
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";

  let { form } = $props();

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<svelte:head>
  <title>{t("cloud.console.title")}</title>
</svelte:head>

<div class="mx-auto mt-16 max-w-sm rounded-xl border border-border bg-surface-raised p-6">
  <h1 class="text-lg font-semibold text-text">{t("cloud.console.sign_in")}</h1>
  <p class="mt-1 text-sm text-text-muted">{t("cloud.console.sign_in_hint")}</p>
  <form method="POST" use:enhance class="mt-5 space-y-4">
    <div>
      <label for="email" class="mb-1 block text-sm font-medium text-text">
        {t("auth.email")}
      </label>
      <input
        id="email"
        name="email"
        type="email"
        required
        autocomplete="email"
        value={form?.email ?? ""}
        class={inputClass}
      />
    </div>
    <div>
      <label for="password" class="mb-1 block text-sm font-medium text-text">
        {t("auth.password")}
      </label>
      <input
        id="password"
        name="password"
        type="password"
        required
        autocomplete="current-password"
        class={inputClass}
      />
    </div>
    {#if form?.error}
      <p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
    {/if}
    <button
      type="submit"
      class="w-full rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
    >
      {t("cloud.console.sign_in")}
    </button>
  </form>
</div>
