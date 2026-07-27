<script lang="ts">
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import ChannelSection from "$lib/modules/notifications/ChannelSection.svelte";
  import PreferenceMatrixForm from "$lib/modules/notifications/PreferenceMatrixForm.svelte";

  let { data, form } = $props();
</script>

<svelte:head>
  <title>{pageTitle(t("settings.notifications.title"))}</title>
</svelte:head>

<div class="mb-6">
  <h1 class="text-xl font-semibold text-text">{t("settings.notifications.title")}</h1>
  <p class="mt-1 text-sm text-text-muted">{t("settings.notifications.subtitle")}</p>
</div>

<PreferenceMatrixForm
  matrix={data.matrix}
  scope="user"
  error={form?.error ?? null}
  saved={form?.saved ?? false}
/>

<!-- One list, under the matrix that routes it: each of these is a column above (#295). -->
{#if data.canManageOwnChannels}
  <ChannelSection channels={data.channels} scope="user" {form} />
{/if}
