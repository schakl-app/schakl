<script lang="ts">
  import "../app.css";
  import { themeStyle } from "$lib/core/theme";

  let { data, children } = $props();

  // hooks.server.ts stamps the brand variables onto <html> for first paint; re-apply them here
  // so saving Huisstijl recolours the running page without a reload.
  $effect(() => {
    document.documentElement.setAttribute("style", themeStyle(data.theme));
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
