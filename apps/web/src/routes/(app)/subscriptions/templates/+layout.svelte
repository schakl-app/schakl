<script lang="ts">
  /**
   * One of the three Instellingen screens that live outside `/settings/` (#229) and so lost the
   * section rail when opened from it. See `$lib/core/settings/SettingsShell.svelte`.
   */
  import SettingsShell from "$lib/core/settings/SettingsShell.svelte";

  let { data, children } = $props();
</script>

<SettingsShell cloud={data.cloud}>
  {@render children()}
</SettingsShell>
