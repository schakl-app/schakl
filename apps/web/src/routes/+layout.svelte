<script lang="ts">
  import "../app.css";
  import { dev } from "$app/environment";
  import { onMount } from "svelte";
  import { registerServiceWorker } from "$lib/core/pwa";
  import { themeStyle } from "$lib/core/theme";
  import { parseThemeCookie } from "$lib/core/theme-mode";
  import { syncResolvedTheme } from "$lib/core/theme-mode.svelte";

  let { data, children } = $props();

  // The one place the PWA's service worker is installed. It belongs in the *root* layout rather
  // than in `(app)`: a worker is registered for the whole origin, and the login screen and the
  // client portal are as much part of the installed app as the dashboard is. See
  // `$lib/core/pwa.ts` for what shipped while nothing did this at all.
  //
  // Skipped in dev because the plugin generates no worker there (`devOptions` is off,
  // `docs/WEBPUSH.md` §7), so asking for one would be a guaranteed 404 in every console.
  onMount(() => {
    if (!dev) void registerServiceWorker();
  });

  function applyBrand() {
    const scheme = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
    document.documentElement.setAttribute("style", themeStyle(data.theme, scheme));
  }

  // hooks.server.ts stamps the brand variables onto <html> for first paint; re-apply them here
  // so saving Huisstijl recolours the running page without a reload. Also re-derives them
  // against the *resolved* colour scheme, since the server-side stamp assumes light when the
  // preference is "system" (it can't know the OS scheme — see hooks.server.ts).
  $effect(() => {
    applyBrand();
    syncResolvedTheme();
  });

  // A "system" preference follows the OS live while the tab is open. An explicit light/dark
  // choice must not be overridden by an OS change — app.css's `dark:` variant already enforces
  // that for CSS; this keeps the JS-driven bits (brand colour, charts) in step with it.
  $effect(() => {
    if ((parseThemeCookie(document.cookie) ?? "system") !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      document.documentElement.dataset.theme = mq.matches ? "dark" : "light";
      applyBrand();
      syncResolvedTheme();
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  });
</script>

<svelte:head>
  <meta name="theme-color" content={data.theme.primaryColor} />
  <link rel="icon" href={data.theme.faviconUrl || "/favicon.svg"} />
  <!-- iOS ignores manifest icons for "Add to Home Screen" and wants a raster apple-touch-icon
       (#198): the tenant's uploaded app icon resized by the API, else the bundled default —
       never a page screenshot. Runtime per-tenant like the favicon above (Golden Rule 4). -->
  <link
    rel="apple-touch-icon"
    sizes="180x180"
    href={data.theme.appIconUrl
      ? `${data.theme.appIconUrl}?size=180`
      : "/icons/apple-touch-icon.png"}
  />
  <!-- Both spellings: the generic one is the standard (Chrome warns without it), the
       apple- one is still what iOS Safari reads. -->
  <meta name="mobile-web-app-capable" content="yes" />
  <meta name="apple-mobile-web-app-capable" content="yes" />
  <meta name="apple-mobile-web-app-status-bar-style" content="default" />
  {#if data.theme.brandName}
    <meta name="apple-mobile-web-app-title" content={data.theme.brandName} />
  {/if}
</svelte:head>

<!-- Brand custom properties live on <html>, not on this wrapper: `accent-color` is inherited
     from :root, so native controls never see an override made further down the tree. -->
<div class="min-h-screen">
  {@render children()}
</div>
