<script lang="ts">
  /**
   * One external channel's delivery cadence (#283).
   *
   * A shared room is not a personal preference, so *when* its events arrive is a property of the
   * channel, not of anyone's matrix: `immediate` pushes each event as its own message, a digest
   * holds them and sends one bundled message per slot. The time-of-day only exists for the daily
   * and weekly cadences, and the weekday only for weekly — showing a "Monday" picker next to an
   * hourly channel would be a question with no answer (docs/UX.md).
   *
   * The controls post themselves (`digest`, `digest_time`, `digest_weekday`) so both the create
   * form and each inline editor can drop this in without threading state back up.
   */
  import { dateLocale } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import TimeInput from "$lib/core/ui/TimeInput.svelte";

  let {
    id,
    digest: initialDigest = "immediate",
    digestTime: initialTime = null,
    digestWeekday: initialWeekday = null,
  }: {
    /** Unique per rendered instance — several editors can be open at once. */
    id: string;
    digest?: string;
    digestTime?: string | null;
    digestWeekday?: number | null;
  } = $props();

  const CADENCES = ["immediate", "hourly", "daily", "weekly"] as const;

  // Monday-based weekday names in the UI locale (2024-01-01 was a Monday).
  const weekdayFmt = new Intl.DateTimeFormat(dateLocale(), { weekday: "long", timeZone: "UTC" });
  const WEEKDAYS = Array.from({ length: 7 }, (_, i) =>
    weekdayFmt.format(new Date(Date.UTC(2024, 0, 1 + i))),
  );

  /** The API returns "HH:MM:SS"; `TimeInput` speaks "HH:MM". */
  const hhmm = (value: string | null | undefined) => (value ? value.slice(0, 5) : "");

  let digest = $state(initialDigest);
  let digestTime = $state(hhmm(initialTime) || "08:00");
  let digestWeekday = $state(initialWeekday ?? 0);

  const scheduled = $derived(digest === "daily" || digest === "weekly");

  const controlClass =
    "rounded-lg border border-border bg-surface-raised px-2 py-1 text-sm outline-none focus:border-brand";
</script>

<div>
  <div class="flex flex-wrap items-end gap-4">
    <label class="block">
      <span class="mb-1 block text-xs font-medium text-text-muted">
        {t("settings.notifications.channel_cadence")}
      </span>
      <select
        id="channel-digest-{id}"
        name="digest"
        class={controlClass}
        bind:value={digest}
        aria-label={t("settings.notifications.channel_cadence")}
      >
        {#each CADENCES as cadence (cadence)}
          <option value={cadence}>{t(`notifications.digest.${cadence}`)}</option>
        {/each}
      </select>
    </label>

    {#if scheduled}
      <div>
        <span class="mb-1 block text-xs font-medium text-text-muted">
          {t("notifications.settings.digest_time")}
        </span>
        <TimeInput name="digest_time" id="channel-digest-time-{id}" bind:value={digestTime} />
      </div>
    {/if}

    {#if digest === "weekly"}
      <label class="block">
        <span class="mb-1 block text-xs font-medium text-text-muted">
          {t("notifications.settings.digest_weekday")}
        </span>
        <select
          id="channel-digest-weekday-{id}"
          name="digest_weekday"
          class={controlClass}
          bind:value={digestWeekday}
        >
          {#each WEEKDAYS as day, i (day)}
            <option value={i}>{day}</option>
          {/each}
        </select>
      </label>
    {/if}
  </div>
  <p class="mt-2 text-xs text-text-muted">{t("settings.notifications.channel_cadence_hint")}</p>
</div>
