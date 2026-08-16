<script lang="ts">
  /**
   * The Instellingen section: every screen under `/settings/` gets the section rail.
   *
   * The shell itself lives in `$lib/core/settings/SettingsShell.svelte` — three settings screens
   * are administered outside this subtree (#229) and a route layout cannot reach them.
   *
   * **Including the index** (issue #378), which used to be special-cased out on the grounds that
   * its cards were the navigation. They are the *descriptions*; forty of them stacked 3050 px tall
   * are not a way to get anywhere, and dropping the rail there meant the one screen you return to
   * in order to find something was the one screen with nothing to find it with. It passes
   * `search={false}` because its own content owns the search over this same list.
   */
  import { page } from "$app/state";
  import SettingsShell from "$lib/core/settings/SettingsShell.svelte";

  let { data, children } = $props();

  const isIndex = $derived(page.url.pathname === "/settings");
</script>

<SettingsShell cloud={data.cloud ?? false} search={!isIndex}>
  {@render children()}
</SettingsShell>
