<script lang="ts">
  import { t } from "$lib/core/i18n";
  import PreferenceMatrixForm from "$lib/modules/notifications/PreferenceMatrixForm.svelte";

  let { data, form } = $props();
</script>

<svelte:head>
  <title>{t("settings.notification_defaults.title")}</title>
</svelte:head>

<div class="mb-6">
  <h1 class="text-xl font-semibold text-text">{t("settings.notification_defaults.title")}</h1>
  <p class="mt-1 text-sm text-text-muted">{t("settings.notification_defaults.subtitle")}</p>
</div>

<PreferenceMatrixForm
  matrix={data.matrix}
  scope="org"
  error={form?.error ?? null}
  saved={form?.saved ?? false}
/>
