<script lang="ts">
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import Button from "$lib/core/ui/Button.svelte";
  import ChannelCadenceFields from "$lib/modules/notifications/ChannelCadenceFields.svelte";
  import ChannelEventFilter from "$lib/modules/notifications/ChannelEventFilter.svelte";
  import PreferenceMatrixForm from "$lib/modules/notifications/PreferenceMatrixForm.svelte";

  let { data, form } = $props();

  const busy = new InFlight();

  // The event vocabulary for the channel filter picker comes from the matrix (already loaded).
  const eventTypes = $derived(data.matrix.events.map((row) => row.event_type));

  // Which existing channel is open in the inline editor (Part 2, #245).
  let editingId = $state("");
  // The filter each open editor is composing, keyed by channel id.
  let editFilter = $state<Record<string, string[]>>({});
  // The filter the create form is composing (empty = all events).
  let createFilter = $state<string[]>([]);

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

  // Open the inline editor for one channel, seeded with its current filter (Part 2, #245).
  function openEditor(channel: { id: string; event_filter: string[] }): void {
    editingId = channel.id;
    editFilter = { ...editFilter, [channel.id]: [...(channel.event_filter ?? [])] };
  }
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
          <li class="px-3 py-2 text-sm">
            <div class="flex items-center gap-3">
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
                <span class="block text-[11px] text-text-muted">
                  {channel.event_filter.length === 0
                    ? t("settings.notifications.channel_events_all")
                    : t("settings.notifications.channel_events_count", {
                        count: channel.event_filter.length,
                      })}
                  · {t(`notifications.digest.${channel.digest}`)}
                </span>
              </div>
              <Button
                variant="secondary"
                size="xs"
                onclick={() => openEditor(channel)}
                disabled={busy.active}>{t("common.edit")}</Button
              >
              <form
                method="POST"
                action="?/testChannel"
                use:enhance={busy.wrap(`test-${channel.id}`)}
              >
                <input type="hidden" name="channel_id" value={channel.id} />
                <Button
                  variant="secondary"
                  size="xs"
                  loading={busy.is(`test-${channel.id}`)}
                  disabled={busy.active}>{t("settings.notifications.channel_test")}</Button
                >
              </form>
              <form
                method="POST"
                action="?/deleteChannel"
                use:enhance={busy.wrap(`delete-${channel.id}`)}
              >
                <input type="hidden" name="channel_id" value={channel.id} />
                <Button
                  variant="danger-outline"
                  size="xs"
                  loading={busy.is(`delete-${channel.id}`)}
                  disabled={busy.active}>{t("common.delete")}</Button
                >
              </form>
            </div>

            {#if editingId === channel.id}
              <form
                method="POST"
                action="?/updateChannel"
                class="mt-3 space-y-3 rounded-lg border border-border p-3"
                use:enhance={busy.wrap(`update-${channel.id}`, () => ({ result, update }) => {
                  if (result.type === "success") editingId = "";
                  void update({ reset: false });
                })}
              >
                <input type="hidden" name="channel_id" value={channel.id} />
                <div class="grid gap-3 sm:grid-cols-2">
                  <div>
                    <label for="edit-name-{channel.id}" class="mb-1 block text-sm text-text"
                      >{t("settings.notifications.channel_name")}</label
                    >
                    <input
                      id="edit-name-{channel.id}"
                      name="name"
                      value={channel.name}
                      required
                      class="w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand"
                    />
                  </div>
                  <label class="flex items-end gap-2 pb-2 text-sm text-text">
                    <input type="checkbox" name="enabled" checked={channel.enabled} />
                    {t("settings.notifications.channel_enabled")}
                  </label>
                </div>
                <ChannelEventFilter
                  events={eventTypes}
                  value={editFilter[channel.id] ?? []}
                  onchange={(v) => (editFilter = { ...editFilter, [channel.id]: v })}
                />
                <input
                  type="hidden"
                  name="event_filter"
                  value={JSON.stringify(editFilter[channel.id] ?? [])}
                />
                <ChannelCadenceFields
                  id={channel.id}
                  digest={channel.digest}
                  digestTime={channel.digest_time}
                  digestWeekday={channel.digest_weekday}
                />
                {#if form?.updateError && form?.updateErrorId === channel.id}
                  <p class="text-sm text-red-600 dark:text-red-400">{t(form.updateError)}</p>
                {/if}
                <div class="flex gap-2">
                  <Button loading={busy.is(`update-${channel.id}`)} disabled={busy.active}
                    >{t("common.save")}</Button
                  >
                  <Button
                    type="button"
                    variant="secondary"
                    onclick={() => (editingId = "")}
                    disabled={busy.active}>{t("common.cancel")}</Button
                  >
                </div>
              </form>
            {/if}
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
      use:enhance={busy.wrap("createChannel", () => ({ result, update }) => {
        if (result.type === "success") createFilter = [];
        void update({ reset: result.type === "success" });
      })}
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

      <ChannelEventFilter
        events={eventTypes}
        value={createFilter}
        onchange={(v) => (createFilter = v)}
      />
      <input type="hidden" name="event_filter" value={JSON.stringify(createFilter)} />

      <ChannelCadenceFields id="new" />

      {#if form?.channelError}
        <p class="text-sm text-red-600 dark:text-red-400">{t(form.channelError)}</p>
      {/if}
      <Button loading={busy.is("createChannel")} disabled={busy.active}
        >{t("settings.notifications.channel_add")}</Button
      >
    </form>
  </section>
{/if}
