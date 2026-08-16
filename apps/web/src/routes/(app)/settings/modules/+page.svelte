<script lang="ts">
  /**
   * Instellingen → Modules: what schakl itself does for this workspace (CLAUDE.md §6a).
   *
   * The integrations moved to a screen of their own (#378). One list holding both said the two
   * were the same decision, and they are not: a module is switched on and works, an integration is
   * switched on and then needs a credential somebody else issues. Keeping them together also put
   * the switch for the whole Integraties group on a page called Modules, while a *group heading*
   * further down the index read "Modules" too — one word, two meanings, on one page.
   *
   * Everything hard is shared with the other screen (`EnablementForm`): the two edit one list.
   */
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import EnablementForm from "$lib/core/settings/EnablementForm.svelte";
  import { pageTitle } from "$lib/core/title";

  let { data, form } = $props();
</script>

<svelte:head>
  <title>{pageTitle(t("settings.modules.title"))}</title>
</svelte:head>

<div class="mb-6">
  <h1 class="mt-2 text-xl font-semibold text-text">{t("settings.modules.title")}</h1>
  <p class="mt-1 text-sm text-text-muted">{t("settings.modules.subtitle")}</p>
</div>

<EnablementForm
  {data}
  {form}
  kind="module"
  isInstanceOwner={page.data.user?.isInstanceOwner ?? false}
/>
