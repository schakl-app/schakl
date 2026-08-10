<script lang="ts">
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import ChannelSection from "$lib/modules/notifications/ChannelSection.svelte";
  import PreferenceMatrixForm from "$lib/modules/notifications/PreferenceMatrixForm.svelte";
  import PushSection from "$lib/modules/notifications/PushSection.svelte";

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

<!-- This browser's own enrolment. Above the channel list because it needs no configuring: it is
     a permission and a device, not a URL somebody pastes (#309). Which events actually push is
     the `web_push` column in the matrix, exactly like e-mail's — the two are separate decisions. -->
<PushSection />

<!-- One list, under the matrix that routes it: each of these is a column above (#295). -->
{#if data.canManageOwnChannels}
  <ChannelSection channels={data.channels} scope="user" {form} />
{/if}
