<script lang="ts">
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import ChannelSection from "$lib/modules/notifications/ChannelSection.svelte";
  import PreferenceMatrixForm from "$lib/modules/notifications/PreferenceMatrixForm.svelte";

  let { data, form } = $props();

  // The event vocabulary for the channel filter picker comes from the matrix (already loaded).
  const eventTypes = $derived(data.matrix.events.map((row) => row.event_type));
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

<!-- My own transports first: they are the ones the matrix above just gained a column for. -->
{#if data.canManageOwnChannels}
  <ChannelSection channels={data.myChannels} {eventTypes} personal {form} />
{/if}

{#if data.canManageChannels}
  <ChannelSection channels={data.channels} {eventTypes} {form} />
{/if}
