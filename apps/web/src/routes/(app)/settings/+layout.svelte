<script lang="ts">
  /**
   * The Instellingen section: every screen under `/settings/` gets the section rail.
   *
   * The shell itself lives in `$lib/core/settings/SettingsShell.svelte` — three settings screens
   * are administered outside this subtree (#229) and a route layout cannot reach them.
   *
   * The index renders no rail: its cards *are* the navigation, with subtitles the rail has no
   * room for.
   */
  import { page } from "$app/state";
  import SettingsShell from "$lib/core/settings/SettingsShell.svelte";

  let { data, children } = $props();

  const isIndex = $derived(page.url.pathname === "/settings");
</script>

{#if isIndex}
  {@render children()}
{:else}
  <SettingsShell cloud={data.cloud ?? false}>
    {@render children()}
  </SettingsShell>
{/if}
