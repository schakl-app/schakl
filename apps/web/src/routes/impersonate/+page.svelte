<script lang="ts">
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";

  // Two states, and a successful claim is neither: that redirects straight from the load (#288).
  // What is left is "the link didn't work" — which before this fix showed up as this host's login
  // screen, reading like a wrong password rather than a spent link — and "you just stopped", the
  // landing an operator gets on a hostname where they have no account of their own.
  let { data } = $props();

  const title = $derived(
    data.stopped ? t("instance.handoff_stopped") : t("instance.handoff_failed"),
  );
</script>

<svelte:head>
  <title>{pageTitle(title)}</title>
</svelte:head>

<div class="flex min-h-screen items-center justify-center px-4">
  <div
    class="w-full max-w-md rounded-2xl border border-border bg-surface-raised p-8 text-center"
    data-testid={data.stopped ? "impersonation-stopped" : "impersonation-handoff-failed"}
  >
    <h1 class="text-lg font-semibold text-text">{title}</h1>
    {#if data.stopped}
      <p class="mt-2 text-sm text-text-muted">{t("instance.handoff_stopped_hint")}</p>
    {:else}
      <p class="mt-2 text-sm text-text-muted">{t(data.error ?? "errors.server")}</p>
      <p class="mt-4 text-sm text-text-muted">{t("instance.handoff_failed_retry")}</p>
    {/if}
    {#if data.consoleUrl}
      <a
        href={data.consoleUrl}
        class="mt-4 inline-block text-sm text-brand underline"
        data-sveltekit-reload
      >
        {t("instance.handoff_back_to_console")}
      </a>
    {/if}
  </div>
</div>
