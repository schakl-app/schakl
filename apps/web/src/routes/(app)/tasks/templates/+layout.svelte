<script lang="ts">
  /**
   * Taaksjablonen is listed in Instellingen under "Modules & werkprocessen", but the catalog staff
   * touch day-to-day lives on the tasks page (#229). Clicking it in the settings rail therefore
   * dropped you out of the section and took the menu with it — the rail exists to say what else
   * lives in Instellingen, and it went quiet on the three screens that are not under `/settings/`.
   *
   * A route layout under `/settings/` cannot wrap a route outside its own subtree, so the shell
   * comes to the screen instead. The tasks tab row still renders inside it: this page is a tab of
   * the tasks section *and* an Instellingen screen, and both ways in stay true.
   */
  import SettingsShell from "$lib/core/settings/SettingsShell.svelte";

  let { data, children } = $props();
</script>

<SettingsShell cloud={data.cloud}>
  {@render children()}
</SettingsShell>
