<script lang="ts">
  import { enhance } from "$app/forms";
  import { dateLocale } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import PreferenceMatrixForm from "$lib/modules/notifications/PreferenceMatrixForm.svelte";

  let { data, form } = $props();

  // --- personal e-mail delivery (#17): off / immediate / daily / weekly ------------------ #
  const EMAIL_MODES = ["off", "immediate", "daily", "weekly"] as const;
  let emailModeChosen = $state("");
  const emailMode = $derived(
    emailModeChosen || (data.emailPref?.enabled ? (data.emailPref.digest ?? "daily") : "off"),
  );

  // Monday-based weekday names in the UI locale (2024-01-01 was a Monday).
  const weekdayFmt = new Intl.DateTimeFormat(dateLocale(), { weekday: "long", timeZone: "UTC" });
  const WEEKDAYS = Array.from({ length: 7 }, (_, i) =>
    weekdayFmt.format(new Date(Date.UTC(2024, 0, 1 + i))),
  );

  // --- external channels (#17): provider cards, never a raw Apprise URL ------------------ #
  // Each kind's guided form: what the admin pastes, in the provider's own vocabulary.
  const CHANNEL_KINDS = [
    "email",
    "slack",
    "msteams",
    "gchat",
    "discord",
    "telegram",
    "webhook",
    "custom",
  ] as const;
  const KIND_PLACEHOLDER: Record<string, string> = {
    slack: "https://hooks.slack.com/services/…",
    msteams: "https://….webhook.office.com/webhookb2/…",
    gchat: "https://chat.googleapis.com/v1/spaces/…",
    discord: "https://discord.com/api/webhooks/…",
    webhook: "https://…",
    custom: "slack://token/#channel",
  };
  let kindChosen = $state("");
  const kind = $derived(kindChosen || "email");
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

<!-- My e-mail delivery (#17): one cadence for everything that reaches me. -->
<section class="mt-8 rounded-xl border border-border bg-surface-raised p-6">
  <h2 class="mb-1 text-sm font-semibold text-text">{t("settings.notifications.email.title")}</h2>
  <p class="mb-4 text-sm text-text-muted">{t("settings.notifications.email.subtitle")}</p>

  <form method="POST" action="?/saveEmailPref" use:enhance class="space-y-4">
    <input type="hidden" name="mode" value={emailMode} />
    <div class="grid gap-2 sm:grid-cols-4">
      {#each EMAIL_MODES as mode (mode)}
        <button
          type="button"
          class="rounded-lg border px-3 py-2 text-sm {emailMode === mode
            ? 'border-brand bg-surface text-brand'
            : 'border-border text-text hover:border-brand'}"
          aria-pressed={emailMode === mode}
          onclick={() => (emailModeChosen = mode)}
        >
          {t(`settings.notifications.email.mode.${mode}`)}
        </button>
      {/each}
    </div>

    {#if emailMode === "daily" || emailMode === "weekly"}
      <div class="flex flex-wrap items-end gap-4">
        {#if emailMode === "weekly"}
          <div>
            <label for="email-digest-weekday" class="mb-1 block text-sm text-text"
              >{t("settings.notifications.email.weekday")}</label
            >
            <select
              id="email-digest-weekday"
              name="digest_weekday"
              class="rounded-lg border border-border px-3 py-2 text-sm"
            >
              {#each WEEKDAYS as day, i (day)}
                <option value={i} selected={(data.emailPref?.digest_weekday ?? 0) === i}
                  >{day}</option
                >
              {/each}
            </select>
          </div>
        {/if}
        <div>
          <label for="email-digest-time" class="mb-1 block text-sm text-text"
            >{t("settings.notifications.email.time")}</label
          >
          <input
            id="email-digest-time"
            name="digest_time"
            type="time"
            value={data.emailPref?.digest_time?.slice(0, 5) ?? "08:00"}
            class="rounded-lg border border-border px-3 py-2 text-sm"
          />
        </div>
      </div>
    {/if}

    <div class="flex items-center gap-3">
      <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >{t("common.save")}</button
      >
      {#if form?.emailPrefSaved}
        <span class="text-sm text-green-700 dark:text-green-400"
          >{t("settings.notifications.email.saved")}</span
        >
      {:else if form?.emailPrefError}
        <span class="text-sm text-red-600 dark:text-red-400">{t(form.emailPrefError)}</span>
      {/if}
    </div>
    <p class="text-xs text-text-muted">{t("settings.notifications.email.transport_hint")}</p>
  </form>
</section>

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
              <span class="ml-2 rounded-full bg-surface px-2 py-0.5 text-[11px] text-text-muted"
                >{t(`settings.notifications.kind.${channel.kind}`)}</span
              >
              {#if !channel.enabled}
                <span class="ml-1 text-[11px] text-text-muted"
                  >({t("settings.notifications.channel_disabled")})</span
                >
              {/if}
              <span class="block truncate font-mono text-xs text-text-muted"
                >{channel.redacted}</span
              >
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
      <p
        class="mb-3 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700 dark:bg-red-950 dark:text-red-300"
      >
        {t("settings.notifications.channel_test_failed", { error: form.testError })}
      </p>
    {:else if form?.testOk}
      <p
        class="mb-3 rounded-lg bg-green-50 px-3 py-2 text-xs text-green-700 dark:bg-green-950 dark:text-green-300"
      >
        {t("settings.notifications.channel_test_ok")}
      </p>
    {/if}

    <h3 class="mb-2 text-xs font-semibold uppercase tracking-wide text-text-muted">
      {t("settings.notifications.channel_add")}
    </h3>
    <div class="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
      {#each CHANNEL_KINDS as k (k)}
        <button
          type="button"
          class="rounded-lg border px-3 py-2 text-sm {kind === k
            ? 'border-brand bg-surface text-brand'
            : 'border-border text-text hover:border-brand'}"
          aria-pressed={kind === k}
          onclick={() => (kindChosen = k)}
        >
          {t(`settings.notifications.kind.${k}`)}
        </button>
      {/each}
    </div>

    <form
      method="POST"
      action="?/createChannel"
      class="space-y-3"
      use:enhance={() =>
        ({ result, update }) => {
          void update({ reset: result.type === "success" });
        }}
    >
      <input type="hidden" name="kind" value={kind} />
      <div class="grid gap-3 sm:grid-cols-2">
        <div>
          <label for="channel-name" class="mb-1 block text-sm text-text"
            >{t("settings.notifications.channel_name")}</label
          >
          <input
            id="channel-name"
            name="name"
            required
            class="w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand"
          />
        </div>
        {#if kind === "email"}
          <div>
            <label for="channel-url" class="mb-1 block text-sm text-text"
              >{t("settings.notifications.input.address")}</label
            >
            <input
              id="channel-url"
              name="url"
              type="email"
              required
              placeholder="team@bureau.nl"
              class="w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand"
            />
          </div>
        {:else if kind === "telegram"}
          <div>
            <label for="channel-bot-token" class="mb-1 block text-sm text-text"
              >{t("settings.notifications.input.bot_token")}</label
            >
            <input
              id="channel-bot-token"
              name="bot_token"
              required
              placeholder="123456:ABC-…"
              class="w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand"
            />
          </div>
          <div>
            <label for="channel-chat-id" class="mb-1 block text-sm text-text"
              >{t("settings.notifications.input.chat_id")}</label
            >
            <input
              id="channel-chat-id"
              name="chat_id"
              required
              class="w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand"
            />
          </div>
        {:else if kind === "custom"}
          <div>
            <label for="channel-url" class="mb-1 block text-sm text-text"
              >{t("settings.notifications.input.apprise_url")}</label
            >
            <input
              id="channel-url"
              name="url"
              required
              placeholder={KIND_PLACEHOLDER.custom}
              class="w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand"
            />
          </div>
        {:else}
          <div>
            <label for="channel-url" class="mb-1 block text-sm text-text"
              >{t("settings.notifications.input.webhook_url")}</label
            >
            <input
              id="channel-url"
              name="url"
              type="url"
              required
              placeholder={KIND_PLACEHOLDER[kind]}
              class="w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand"
            />
          </div>
        {/if}
      </div>
      <p class="text-xs text-text-muted">{t(`settings.notifications.kind_hint.${kind}`)}</p>
      {#if form?.channelError}
        <p class="text-sm text-red-600 dark:text-red-400">{t(form.channelError)}</p>
      {/if}
      <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >{t("settings.notifications.channel_add")}</button
      >
    </form>
  </section>
{/if}
