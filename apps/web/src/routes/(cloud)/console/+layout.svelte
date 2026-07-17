<script lang="ts">
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";

  let { data, children } = $props();

  const navClass = (href: string) =>
    page.url.pathname === href
      ? "rounded-lg bg-surface px-3 py-1.5 text-sm font-medium text-text"
      : "rounded-lg px-3 py-1.5 text-sm font-medium text-text-muted hover:text-text";
</script>

<div class="min-h-screen bg-surface-sunken">
  <header class="border-b border-border bg-surface-raised">
    <div class="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-3 px-4 py-3">
      <div class="flex items-center gap-3">
        <span class="text-base font-semibold text-text">{t("cloud.console.title")}</span>
        <span class="font-mono text-xs text-text-muted">{data.meta.baseDomain}</span>
      </div>
      {#if data.me?.isInstanceAdmin}
        <nav class="flex items-center gap-1">
          <a href="/console" class={navClass("/console")}>{t("cloud.console.nav_orgs")}</a>
          <a href="/console/keys" class={navClass("/console/keys")}>
            {t("cloud.console.nav_keys")}
          </a>
          <span class="mx-2 hidden text-xs text-text-muted sm:inline">{data.me.email}</span>
          <a
            href="/logout"
            data-sveltekit-preload-data="off"
            class="rounded-lg border border-border px-3 py-1.5 text-sm font-medium text-text hover:bg-surface"
          >
            {t("cloud.console.sign_out")}
          </a>
        </nav>
      {/if}
    </div>
  </header>
  <main class="mx-auto max-w-5xl px-4 py-8">
    {@render children()}
  </main>
</div>
