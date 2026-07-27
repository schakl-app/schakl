<script lang="ts">
  /**
   * When a channel's digests land (#283, #295).
   *
   * No channel has a cadence of its own — the matrix sets one per event — but a daily or weekly
   * digest still needs an hour and a day. Asking once per channel beats asking on every one of
   * twenty-odd matrix rows, so the schedule lives here and the cadence lives there.
   */
  import { untrack } from "svelte";

  import { dateLocale } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import TimeInput from "$lib/core/ui/TimeInput.svelte";

  let {
    id,
    digestTime: initialTime = null,
    digestWeekday: initialWeekday = null,
  }: {
    /** Unique per rendered instance — several editors can be open at once. */
    id: string;
    digestTime?: string | null;
    digestWeekday?: number | null;
  } = $props();

  // Monday-based weekday names in the UI locale (2024-01-01 was a Monday).
  const weekdayFmt = new Intl.DateTimeFormat(dateLocale(), { weekday: "long", timeZone: "UTC" });
  const WEEKDAYS = Array.from({ length: 7 }, (_, i) =>
    weekdayFmt.format(new Date(Date.UTC(2024, 0, 1 + i))),
  );

  /** The API returns "HH:MM:SS"; `TimeInput` speaks "HH:MM". */
  const hhmm = (value: string | null | undefined) => (value ? value.slice(0, 5) : "");

  // Seeded from the props, then owned here (the controls post themselves).
  let digestTime = $state(untrack(() => hhmm(initialTime) || "08:00"));
  let digestWeekday = $state(untrack(() => initialWeekday ?? 0));

  const controlClass =
    "rounded-lg border border-border bg-surface-raised px-2 py-1 text-sm outline-none focus:border-brand";
</script>

<div>
  <div class="flex flex-wrap items-end gap-4">
    <div>
      <span class="mb-1 block text-xs font-medium text-text-muted">
        {t("notifications.settings.digest_time")}
      </span>
      <TimeInput name="digest_time" id="channel-schedule-time-{id}" bind:value={digestTime} />
    </div>
    <label class="block">
      <span class="mb-1 block text-xs font-medium text-text-muted">
        {t("notifications.settings.digest_weekday")}
      </span>
      <select
        id="channel-schedule-weekday-{id}"
        name="digest_weekday"
        class={controlClass}
        bind:value={digestWeekday}
      >
        {#each WEEKDAYS as day, i (day)}
          <option value={i}>{day}</option>
        {/each}
      </select>
    </label>
  </div>
  <p class="mt-2 text-xs text-text-muted">{t("settings.notifications.channel_schedule_hint")}</p>
</div>
