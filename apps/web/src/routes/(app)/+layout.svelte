<script lang="ts">
  import "$lib/modules"; // ensure the web-module registry is populated
  import { page } from "$app/state";
  import { navItemsFor } from "$lib/core/registry";
  import { LOCALES, localeLabel, t } from "$lib/core/i18n";

  let { children } = $props();

  const theme = $derived(page.data.theme);
  const user = $derived(page.data.user);
  const nav = $derived(navItemsFor(theme?.enabledModules ?? []));
  const path = $derived(page.url.pathname);
</script>

<div class="flex min-h-screen">
  <aside class="hidden w-60 shrink-0 border-r border-neutral-200 bg-white sm:block">
    <div class="flex h-14 items-center gap-2 border-b border-neutral-200 px-4">
      {#if theme?.logoUrl}
        <img src={theme.logoUrl} alt={theme.brandName} class="h-7 w-auto" />
      {/if}
      <span class="truncate font-semibold text-neutral-900">{theme?.brandName}</span>
    </div>
    <nav class="space-y-1 p-2">
      <a
        href="/"
        class="block rounded-lg px-3 py-2 text-sm text-neutral-700 hover:bg-neutral-100"
        class:bg-neutral-100={path === "/"}
      >
        {t("nav.dashboard")}
      </a>
      {#each nav as item (item.key)}
        <a
          href={item.href}
          class="block rounded-lg px-3 py-2 text-sm text-neutral-700 hover:bg-neutral-100"
          class:bg-neutral-100={path.startsWith(item.href)}
        >
          {item.label()}
        </a>
      {/each}
    </nav>
  </aside>

  <div class="flex flex-1 flex-col">
    <header
      class="flex h-14 items-center justify-end gap-4 border-b border-neutral-200 bg-white px-6 text-sm"
    >
      <form method="POST" action="/set-locale">
        <input type="hidden" name="redirect" value={path} />
        <select
          name="locale"
          onchange={(e) => e.currentTarget.form?.requestSubmit()}
          class="rounded-lg border border-neutral-300 px-2 py-1"
          aria-label={t("common.language")}
        >
          {#each LOCALES as loc (loc)}
            <option value={loc} selected={page.data.locale === loc}>{localeLabel(loc)}</option>
          {/each}
        </select>
      </form>
      <span class="text-neutral-600">{user?.full_name || user?.email}</span>
      <form method="POST" action="/logout">
        <button class="text-neutral-500 hover:text-neutral-900">{t("auth.sign_out")}</button>
      </form>
    </header>

    <main class="flex-1 p-6">
      {@render children()}
    </main>
  </div>
</div>
