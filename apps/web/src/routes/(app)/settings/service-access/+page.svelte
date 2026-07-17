<script lang="ts">
  import { enhance } from "$app/forms";
  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";

  let { data, form } = $props();
</script>

<svelte:head>
  <title>{pageTitle(t("settings.service_access.title"))}</title>
</svelte:head>

<h1 class="mb-1 mt-2 text-xl font-semibold text-text">{t("settings.service_access.title")}</h1>
<p class="mb-6 text-sm text-text-muted">
  {t("settings.service_access.subtitle", { hours: data.access.pin_hours })}
</p>

<section class="max-w-2xl rounded-xl border border-border bg-surface-raised p-5">
  {#if form?.pin}
    <div
      class="rounded-xl border border-emerald-300 bg-emerald-50 p-4 dark:border-emerald-900 dark:bg-emerald-950/40"
    >
      <p class="text-sm font-medium text-emerald-800 dark:text-emerald-300">
        {t("settings.service_access.pin_hint")}
      </p>
      <code class="mt-2 block font-mono text-2xl tracking-wider text-text">{form.pin}</code>
    </div>
  {/if}

  {#if data.access.active || form?.pin}
    <div class="mt-4 space-y-1 text-sm text-text">
      <p>
        {t("settings.service_access.active_until", {
          until: fmtDateTime(form?.expiresAt ?? data.access.expires_at ?? ""),
        })}
      </p>
      {#if data.access.claimed}
        <p class="text-amber-700 dark:text-amber-400">
          {t("settings.service_access.claimed")}
        </p>
      {:else if !form?.pin}
        <p class="text-text-muted">{t("settings.service_access.unclaimed")}</p>
      {/if}
    </div>
    <form method="POST" action="?/revoke" use:enhance class="mt-4">
      <button
        class="rounded-lg border border-border px-4 py-2 text-sm font-medium text-red-600 hover:bg-surface dark:text-red-400"
      >
        {t("settings.service_access.revoke")}
      </button>
    </form>
  {:else}
    <p class="text-sm text-text-muted">{t("settings.service_access.none")}</p>
  {/if}

  {#if form?.revoked}
    <p class="mt-3 text-sm text-emerald-700 dark:text-emerald-400">
      {t("settings.service_access.revoked")}
    </p>
  {/if}
  {#if form?.error}
    <p class="mt-3 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
  {/if}

  <form method="POST" action="?/generate" use:enhance class="mt-5 border-t border-border pt-4">
    <button
      class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
    >
      {t("settings.service_access.generate")}
    </button>
    <p class="mt-2 text-xs text-text-muted">{t("settings.service_access.generate_hint")}</p>
  </form>
</section>
