<script lang="ts">
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import PreferenceMatrixForm from "$lib/modules/notifications/PreferenceMatrixForm.svelte";

  let { data, form } = $props();

  // "email" sends to an address via the org's own transport (Instellingen → E-mail, #17).
  const CHANNEL_KINDS = [
    "email",
    "slack",
    "msteams",
    "gchat",
    "discord",
    "telegram",
    "mailto",
    "webhook",
  ];
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

{#if data.canManageChannels}
  <section class="mt-8 rounded-xl border border-border bg-surface-raised p-6">
    <h2 class="mb-1 text-sm font-semibold text-text">{t("settings.notifications.channels")}</h2>
    <p class="mb-4 text-sm text-text-muted">{t("settings.notifications.channels_hint")}</p>

    {#if data.channels.length > 0}
      <ul class="mb-4 divide-y divide-border rounded-lg border border-border">
        {#each data.channels as channel (channel.id)}
          <li class="flex items-center gap-3 px-3 py-2 text-sm">
            <div class="min-w-0 flex-1">
              <span class="font-medium text-text">{channel.name}</span>
              <span
                class="ml-2 rounded-full bg-surface px-2 py-0.5 text-[11px] text-text-muted"
                >{channel.kind}</span
              >
              {#if !channel.enabled}
                <span class="ml-1 text-[11px] text-text-muted"
                  >({t("settings.notifications.channel_disabled")})</span
                >
              {/if}
              <span class="block truncate font-mono text-xs text-text-muted">{channel.redacted}</span>
            </div>
            <form method="POST" action="?/testChannel" use:enhance>
              <input type="hidden" name="channel_id" value={channel.id} />
              <button
                class="rounded-lg border border-border px-2.5 py-1.5 text-xs text-text-muted hover:text-text"
                >{t("settings.notifications.channel_test")}</button
              >
            </form>
            <form method="POST" action="?/deleteChannel" use:enhance>
              <input type="hidden" name="channel_id" value={channel.id} />
              <button
                class="rounded-lg px-2 py-1.5 text-xs text-text-muted hover:text-red-600 dark:hover:text-red-400"
                >{t("common.delete")}</button
              >
            </form>
          </li>
        {/each}
      </ul>
    {/if}

    {#if form?.testError}
      <p class="mb-3 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700 dark:bg-red-950 dark:text-red-300">
        {t("settings.notifications.channel_test_failed", { error: form.testError })}
      </p>
    {:else if form?.testOk}
      <p class="mb-3 rounded-lg bg-green-50 px-3 py-2 text-xs text-green-700 dark:bg-green-950 dark:text-green-300">
        {t("settings.notifications.channel_test_ok")}
      </p>
    {/if}

    <form
      method="POST"
      action="?/createChannel"
      class="grid grid-cols-1 gap-3 sm:grid-cols-4"
      use:enhance={() =>
        ({ result, update }) => {
          void update({ reset: result.type === "success" });
        }}
    >
      <select
        name="kind"
        class="rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand"
      >
        {#each CHANNEL_KINDS as kind (kind)}<option value={kind}>{kind}</option>{/each}
      </select>
      <input
        name="name"
        required
        placeholder={t("settings.notifications.channel_name")}
        class="rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand"
      />
      <input
        name="url"
        required
        placeholder={t("settings.notifications.channel_url")}
        class="rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand sm:col-span-2"
      />
      <p class="text-xs text-text-muted sm:col-span-3">{t("settings.notifications.channel_url_hint")}</p>
      <button
        class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >{t("settings.notifications.channel_add")}</button
      >
    </form>
  </section>
{/if}
