<!--
  The error page for a *running* app: the tenant resolved, the layout loaded, this route just has
  nothing to show. It is the one of the three error surfaces that can use the real stylesheet and
  the real branding, so it does (Golden Rule 4) — the other two are string renderers for the
  moments the app or the API is the thing that is missing (`$lib/core/errors/standalone.server`,
  `app/core/errorpage.py`). All three read their wording from `$lib/core/errors/copy`.

  It used to render the raw status over `t(page.error?.message)` — which is a *key* on an API
  error and free text on a SvelteKit one, so half the time it printed "errors.not_found" at the
  visitor. The status is the thing we can always interpret; the message key is kept as the
  precise line when the catalogue actually holds it.
-->
<script lang="ts">
  import { page } from "$app/state";
  import { errorCopy } from "$lib/core/errors/copy";
  import { hasMessage, t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";

  const copy = $derived(errorCopy(page.status));
  const brand = $derived(page.data.theme?.brandName ?? "");
  const logo = $derived(page.data.theme?.logoUrl ?? null);

  /**
   * A specific message beats a generic one, but only when it is one of ours: an API envelope
   * hands us an i18n key (`errors.license_required` says far more than "er ging iets mis"),
   * while SvelteKit's own errors carry English prose that must never reach a Dutch screen.
   * `hasMessage` is what tells them apart — `t()` alone degrades a miss to the key itself.
   */
  const detail = $derived(
    page.error?.message && hasMessage(page.error.message) ? t(page.error.message) : null,
  );
</script>

<svelte:head>
  <title>{pageTitle(t(copy.titleKey))}</title>
  <meta name="robots" content="noindex" />
</svelte:head>

<div class="flex min-h-screen items-center justify-center px-4">
  <div
    class="w-full max-w-sm rounded-2xl border border-border bg-surface-raised p-8 text-center shadow-sm"
  >
    {#if logo}
      <img src={logo} alt={brand} class="mx-auto mb-6 max-h-8 max-w-36 object-contain" />
    {:else if brand}
      <p class="mb-6 text-sm font-semibold text-text">{brand}</p>
    {/if}

    <h1 class="text-lg font-semibold text-text">{t(copy.titleKey)}</h1>
    <p class="mt-2 text-sm text-text-muted">{detail ?? t(copy.bodyKey)}</p>

    {#if copy.retryable}
      <!-- A full document load, not a client-side navigation: the thing that failed is the
           server, so re-running the same failed load in the same page proves nothing. -->
      <a
        href={page.url.pathname + page.url.search}
        data-sveltekit-reload
        class="mt-6 inline-block text-sm font-medium text-brand underline underline-offset-2"
      >
        {t("errors.page.retry")}
      </a>
    {:else}
      <a
        href="/"
        class="mt-6 inline-block text-sm font-medium text-brand underline underline-offset-2"
      >
        {t("errors.page.home")}
      </a>
    {/if}

    <p class="mt-5 text-xs uppercase tracking-wide text-text-muted">
      {t("errors.page.code", { status: page.status })}
    </p>
  </div>
</div>
