<script lang="ts">
  import "../app.css";
  import { themeStyle } from "$lib/core/theme";

  let { data, children } = $props();

  const style = $derived(themeStyle(data.theme));
</script>

<svelte:head>
  <meta name="theme-color" content={data.theme.primaryColor} />
  {#if data.theme.faviconUrl}
    <link rel="icon" href={data.theme.faviconUrl} />
  {/if}
</svelte:head>

<div style={style} class="min-h-screen">
  {@render children()}
</div>
