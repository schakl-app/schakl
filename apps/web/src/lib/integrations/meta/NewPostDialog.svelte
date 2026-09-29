<script lang="ts">
  /**
   * The first question about a post: **where does it go?** Asked before the post exists,
   * because the answer decides whose post it is and what each channel will accept — and
   * because the words are better written on a page than in a dialog (#230, create-then-edit).
   *
   * Two picks at most. The client narrows the channels (a post is for one client, and the API
   * refuses one that names two), and the channels are toggles with everything that can be
   * published to switched on already: the common case is "all of this client's", and a
   * default that has to be ticked four times is a form, not a default.
   *
   * When there is only one group to choose from, the client picker is not drawn at all.
   */
  import { enhance } from "$app/forms";

  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import TimeInput from "$lib/core/ui/TimeInput.svelte";

  import ChannelChips from "./ChannelChips.svelte";
  import type { ChannelOption } from "./types";

  /** The value that stands for "the agency's own channels" in the client picker. */
  const OWN = "own";

  let {
    open = $bindable(false),
    channels,
    companyId = "",
    day = "",
    error = null,
    action = "/marketing/social?/create",
  }: {
    open?: boolean;
    channels: ChannelOption[];
    /** Preselect a client — the planner's own filter, so "new" continues what was on screen. */
    companyId?: string;
    /** Preselect a day — the agenda's ＋ hands over the one that was clicked. */
    day?: string;
    error?: string | null;
    action?: string;
  } = $props();

  const busy = new InFlight();

  const groups = $derived.by(() => {
    // A plain record: built and read inside one derivation, so nothing here needs to be
    // reactive on its own.
    const seen: Record<string, string> = {};
    for (const channel of channels) {
      const key = channel.companyId ?? OWN;
      seen[key] ??= channel.companyId ? (channel.companyName ?? "") : t("meta.own_channels");
    }
    return Object.entries(seen)
      .map(([value, label]) => ({ value, label }))
      .toSorted((a, b) =>
        a.value === OWN ? 1 : b.value === OWN ? -1 : a.label.localeCompare(b.label),
      );
  });

  let group = $state("");
  let picked = $state<string[]>([]);
  let chosenDay = $state("");
  let chosenTime = $state("09:00");
  let format = $state("post");

  const offered = $derived(channels.filter((channel) => (channel.companyId ?? OWN) === group));

  function choose(value: string): void {
    group = value;
    picked = channels
      .filter((channel) => (channel.companyId ?? OWN) === value && channel.canPublish)
      .map((channel) => channel.id);
  }

  // Each opening starts from what the caller handed over, not from the last post's answers.
  let wasOpen = false;
  $effect(() => {
    if (open && !wasOpen) {
      const wanted = companyId && groups.some((g) => g.value === companyId) ? companyId : "";
      choose(wanted || (groups.length === 1 ? groups[0].value : ""));
      chosenDay = day;
      chosenTime = "09:00";
      format = "post";
    }
    wasOpen = open;
  });
</script>

<Modal bind:open title={t("meta.new.title")} size="xl">
  <!-- clear(): a create form starts something new. On success the action redirects to the
       post's own page, so there is nothing here to put back. -->
  <form method="POST" {action} use:enhance={busy.clear("create")} class="space-y-5">
    {#if groups.length > 1}
      <div>
        <label for="new-post-client" class="mb-1 block text-sm font-medium text-text">
          {t("meta.new.client")}
        </label>
        <Combobox
          items={groups}
          name="group"
          id="new-post-client"
          value={group}
          allowEmpty={false}
          placeholder={t("meta.new.client_placeholder")}
          onselect={choose}
        />
      </div>
    {/if}

    {#if group}
      <div>
        <p class="mb-1.5 text-sm font-medium text-text">{t("meta.composer.channels")}</p>
        <ChannelChips channels={offered} bind:selected={picked} />
        {#if picked.length === 0}
          <p class="mt-1.5 text-xs text-text-muted">{t("meta.issue.no_channel")}</p>
        {/if}
      </div>

      <fieldset>
        <legend class="mb-1.5 text-sm font-medium text-text">{t("meta.new.format")}</legend>
        <div class="flex flex-wrap gap-4 text-sm text-text">
          <label class="flex items-center gap-2">
            <input type="radio" name="format" value="post" bind:group={format} />
            {t("meta.format.post")}
          </label>
          <label class="flex items-center gap-2">
            <input type="radio" name="format" value="reel" bind:group={format} />
            {t("meta.format.reel")}
          </label>
        </div>
        <p class="mt-1 text-xs text-text-muted">
          {format === "reel" ? t("meta.format.reel_hint") : t("meta.format.post_hint")}
        </p>
      </fieldset>

      <div>
        <p class="mb-1.5 text-sm font-medium text-text">{t("meta.new.when")}</p>
        <div class="flex flex-wrap items-center gap-2">
          <div class="w-44"><DateInput name="day" bind:value={chosenDay} /></div>
          <div class="w-28"><TimeInput name="time" bind:value={chosenTime} /></div>
        </div>
        <p class="mt-1 text-xs text-text-muted">{t("meta.new.when_hint")}</p>
      </div>
    {/if}

    {#if error}
      <p class="text-sm text-red-600 dark:text-red-400" role="alert">{t(error)}</p>
    {/if}

    <div class="flex items-center justify-end gap-2 border-t border-border pt-4">
      <Button type="button" variant="secondary" onclick={() => (open = false)}>
        {t("common.cancel")}
      </Button>
      <Button
        type="submit"
        loading={busy.is("create")}
        disabled={busy.active || picked.length === 0}
      >
        {t("meta.new.submit")}
      </Button>
    </div>
  </form>
</Modal>
