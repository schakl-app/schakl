<script lang="ts">
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";

  // Deliberately NOT imported from $lib/cloud/instance: that module pulls in `apiFor`, i.e.
  // the server-side session helpers, and importing it here drags them into the client bundle.
  // `svelte-check` passes on it; the production build does not (`pnpm web build` fails at the
  // service-worker step with no client output to glob). Keep this a local, pure check.
  //
  // It mirrors the API's own gate and decides only what to *render* — the endpoint is the
  // boundary (CLAUDE.md §15, "the frontend guard is UX, not security").
  const KEYS_MANAGE = "instance.keys.manage";
  const holds = (capability: string) =>
    !!data.me && (data.me.isInstanceOwner || data.me.capabilities.includes(capability));

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
          {#if holds(KEYS_MANAGE)}
            <a href="/console/keys" class={navClass("/console/keys")}>
              {t("cloud.console.nav_keys")}
            </a>
          {/if}
          {#if data.me?.isInstanceOwner}
            <!-- Owner-only: managing who may cross tenants is deliberately not a delegable
                 capability (#26), so this hangs off the owner principal, not on can(). -->
            <a href="/console/admins" class={navClass("/console/admins")}>
              {t("cloud.console.nav_admins")}
            </a>
          {/if}
          <span class="mx-2 hidden text-xs text-text-muted sm:inline">{data.me.email}</span>
          <!-- A form, not a link: `/logout` is POST-only on purpose (audit F26 — a GET that
               ends a session is triggerable by any image tag on any page), so the anchor that
               used to be here answered 405 and the console could not be signed out of at all. -->
          <form method="POST" action="/logout">
            <button
              class="rounded-lg border border-border px-3 py-1.5 text-sm font-medium text-text hover:bg-surface"
            >
              {t("cloud.console.sign_out")}
            </button>
          </form>
        </nav>
      {/if}
    </div>
  </header>
  <main class="mx-auto max-w-5xl px-4 py-8">
    {@render children()}
  </main>
</div>
