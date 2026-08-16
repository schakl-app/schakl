<script lang="ts">
  /**
   * Instellingen → Integraties: the services this workspace talks to (CLAUDE.md §6a, issue #378).
   *
   * It sits at the head of the Integraties group, above the eight screens that each hold one
   * credential — which is the whole reason it exists as a separate screen. Before, the group was
   * a list of settings for connections with no way to make one, and the switch that would have
   * made one lived under Werkruimte on a page called Modules.
   */
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import EnablementForm from "$lib/core/settings/EnablementForm.svelte";
  import { pageTitle } from "$lib/core/title";

  let { data, form } = $props();
</script>

<svelte:head>
  <title>{pageTitle(t("settings.integrations.title"))}</title>
</svelte:head>

<div class="mb-6">
  <h1 class="mt-2 text-xl font-semibold text-text">{t("settings.integrations.title")}</h1>
  <p class="mt-1 text-sm text-text-muted">{t("settings.integrations.subtitle")}</p>
</div>

<EnablementForm
  {data}
  {form}
  kind="integration"
  isInstanceOwner={page.data.user?.isInstanceOwner ?? false}
/>
