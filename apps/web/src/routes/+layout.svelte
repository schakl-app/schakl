<script lang="ts">
  import "../app.css";
  import { themeStyle } from "$lib/core/theme";
  import { parseThemeCookie } from "$lib/core/theme-mode";
  import { syncResolvedTheme } from "$lib/core/theme-mode.svelte";

  let { data, children } = $props();

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
</svelte:head>

<!-- Brand custom properties live on <html>, not on this wrapper: `accent-color` is inherited
     from :root, so native controls never see an override made further down the tree. -->
<div class="min-h-screen">
  {@render children()}
</div>
