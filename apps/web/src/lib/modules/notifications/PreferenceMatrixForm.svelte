<script lang="ts">
  /**
   * The delivery matrix (issue #16; e-mail per event in #245, external channels in #283 and #295)
   * — one surface, one save button (docs/UX.md).
   *
   * Every event is deliverable on the bell (in-app), on e-mail, and on each external channel this
   * scope owns — one column each, every one with its own cadence per event. Which channels those
   * are is the page's business, not this component's: my own transports on Instellingen →
   * Meldingen, the org's shared rooms on Instellingen → Standaard meldingen. A channel appearing
   * as a column is the *whole* of how it is routed, which is why connecting one adds no code here.
   *
   * The two **implicit** channels resolve in three layers (default ← org row ← user row, **whole
   * rows at a time**). A save must not quietly turn every inherited row into an override, or
   * after one click nothing would inherit again and tomorrow's better default could never reach
   * anyone. So this posts, per channel, only the events that already override at this scope plus
   * the ones actually changed here; everything else keeps falling through, and the badge says
   * where each value came from.
   *
   * An **external channel** has no such layering — a channel belongs to exactly one scope, so
   * there is nothing to inherit from — and its column is plain: a row exists (routed, at a
   * cadence) or it does not (silent). Its block is posted wholesale, carrying only the events
   * actually routed there. That is also why it needs no source badge.
   *
   * E-mail and every external channel are a subset of in-app: they fan out from the freshly
   * written bell rows, so an event switched off in-app cannot reach them whatever their own
   * column says. Those cells are therefore disabled where in-app is off — the stored value is
   * kept, it simply cannot fire.
   *
   * Edits live in a sparse `edits` map layered over the loaded matrix, rather than a copy of it:
   * changing a control and changing it back leaves the row inherited instead of freezing today's
   * default into a row of its own.
   *
   * "Reset" posts an empty set, which deletes this scope's rows — the API's own meaning of reset.
   *
   * Deliberately not exposed: an in-app digest's time-of-day and weekday (the columns exist and
   * fall back to 08:00 on the org's clock, on Monday). The e-mail digest schedule *is* exposed, as
   * one global choice, because e-mail leaves the app and when it lands is worth controlling.
   */
  import { enhance } from "$app/forms";
  import type { SubmitFunction } from "@sveltejs/kit";

  import { dateLocale } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import TimeInput from "$lib/core/ui/TimeInput.svelte";

  // Mirrors the generated client, whose optional fields carry the API's own defaults.
  interface Row {
    event_type: string;
    enabled: boolean;
    delay_minutes: number;
    digest: string;
    digest_time?: string | null;
    digest_weekday?: number | null;
    source: string;
    email_enabled: boolean;
    email_delay_minutes: number;
    email_digest: string;
    email_source: string;
    push_enabled: boolean;
    push_delay_minutes: number;
    push_digest: string;
    push_source: string;
  }
  interface General {
    due_soon_days: number;
    quiet_hours_start?: string | null;
    quiet_hours_end?: string | null;
    source: string;
  }
  interface EmailSchedule {
    digest_time?: string | null;
    digest_weekday?: number | null;
    source: string;
  }
  /** One event's rule on one external channel (#283). */
  interface ChannelEvent {
    event_type: string;
    enabled: boolean;
    delay_minutes: number;
    digest: string;
  }
  interface Channel {
    id: string;
    name: string;
    kind: string;
    digest_time?: string | null;
    digest_weekday?: number | null;
    events?: ChannelEvent[];
  }

  let {
    matrix,
    /** Which layer this screen writes: a user's own row, or the org's default row. */
    scope,
    error = null,
    saved = false,
  }: {
    matrix: {
      events: Row[];
      general: General;
      email: EmailSchedule;
      push: EmailSchedule;
      channels?: Channel[];
    };
    scope: "user" | "org";
    error?: string | null;
    saved?: boolean;
  } = $props();

  const CADENCES = ["immediate", "hourly", "daily", "weekly"] as const;
  // Every non-bell column is a single select: "off" plus the four cadences.
  const EMAIL_OPTIONS = ["off", ...CADENCES] as const;

  // Whatever channels this scope owns — my transports, or the org's shared rooms (#295).
  const channels = $derived(matrix.channels ?? []);

  // Monday-based weekday names in the UI locale (2024-01-01 was a Monday).
  const weekdayFmt = new Intl.DateTimeFormat(dateLocale(), { weekday: "long", timeZone: "UTC" });
  const WEEKDAYS = Array.from({ length: 7 }, (_, i) =>
    weekdayFmt.format(new Date(Date.UTC(2024, 0, 1 + i))),
  );

  let edits = $state<Record<string, Partial<Row>>>({});
  let generalEdit = $state<Partial<General>>({});
  let emailEdit = $state<Partial<EmailSchedule>>({});
  let pushEdit = $state<Partial<EmailSchedule>>({});
  /** `{[channel id]: {[event type]: "off" | cadence}}` — sparse, layered over the loaded matrix. */
  let channelEdits = $state<Record<string, Record<string, string>>>({});

  const busy = new InFlight();
  // Save and reset share the form (#279): key off the clicked button's formaction.
  const submit: SubmitFunction = (input) =>
    busy.wrap(
      input.submitter?.getAttribute("formaction") === "?/reset" ? "reset" : "save",
      () =>
        async ({ update }) => {
          // reset: false is load-bearing: the default form reset snaps every checkbox and select
          // back to its server-rendered mark (their DOM default), while the state that drives them
          // already holds the saved value — so Svelte sees nothing to rewrite and the matrix
          // visibly reverts on save, even though the save succeeded.
          await update({ reset: false });
          edits = {}; // the reloaded matrix is now the truth; stale edits must not re-apply
          generalEdit = {};
          emailEdit = {};
          pushEdit = {};
          channelEdits = {};
        },
    )(input);

  const rows = $derived(matrix.events.map((row) => ({ ...row, ...(edits[row.event_type] ?? {}) })));

  // #146: one flat table reads as a wall. Group rows under module headers derived from the
  // event key's prefix — the API's EVENT_TYPES order already clusters by module, so this is
  // purely presentational and a new module's events form their own section for free.
  const groups = $derived.by(() => {
    const out: { key: string; rows: typeof rows }[] = [];
    for (const row of rows) {
      const key = row.event_type.split(".")[0];
      const last = out[out.length - 1];
      if (last && last.key === key) last.rows.push(row);
      else out.push({ key, rows: [row] });
    }
    return out;
  });
  const general = $derived({ ...matrix.general, ...generalEdit });
  const emailSchedule = $derived({ ...matrix.email, ...emailEdit });
  const pushSchedule = $derived({ ...matrix.push, ...pushEdit });

  function baseline(eventType: string): Row | undefined {
    return matrix.events.find((row) => row.event_type === eventType);
  }

  function edit(eventType: string, patch: Partial<Row>): void {
    edits = { ...edits, [eventType]: { ...edits[eventType], ...patch } };
  }

  /** The e-mail column's value: "off" when disabled, else the cadence. */
  const emailValue = (row: Row) => (row.email_enabled ? row.email_digest : "off");
  function editEmail(eventType: string, value: string): void {
    if (value === "off") edit(eventType, { email_enabled: false });
    else edit(eventType, { email_enabled: true, email_digest: value });
  }

  /** Set the e-mail column of every in-app-enabled event at once (a convenience). */
  function applyAllEmail(value: string): void {
    const patch =
      value === "off" ? { email_enabled: false } : { email_enabled: true, email_digest: value };
    const next = { ...edits };
    for (const row of rows) {
      if (!row.enabled) continue; // e-mail can't fire without an in-app row
      next[row.event_type] = { ...next[row.event_type], ...patch };
    }
    edits = next;
  }

  // --- browser push (#309): the e-mail column's twin ------------------------------------- #
  // A third implicit column, resolved and written exactly like e-mail's. Whether this browser
  // is *registered* is the section below the matrix; this is only which events push, and the
  // two are deliberately separate decisions.
  const pushValue = (row: Row) => (row.push_enabled ? row.push_digest : "off");
  function editPush(eventType: string, value: string): void {
    if (value === "off") edit(eventType, { push_enabled: false });
    else edit(eventType, { push_enabled: true, push_digest: value });
  }

  function applyAllPush(value: string): void {
    const patch =
      value === "off" ? { push_enabled: false } : { push_enabled: true, push_digest: value };
    const next = { ...edits };
    for (const row of rows) {
      if (!row.enabled) continue; // push can't fire without an in-app row
      next[row.event_type] = { ...next[row.event_type], ...patch };
    }
    edits = next;
  }

  // --- external channels (#283, #295) ----------------------------------------------------- #
  // One column each, all driven by the same three functions — adding a channel adds no code.

  /** The loaded value of one cell: the cadence it is routed at, or "off". */
  const channelBaseline = (channel: Channel, eventType: string): string => {
    const row = (channel.events ?? []).find((e) => e.event_type === eventType);
    return row?.enabled ? row.digest : "off";
  };

  /** What the cell shows now: the edit if there is one, else what loaded. */
  const channelValue = (channel: Channel, eventType: string): string =>
    channelEdits[channel.id]?.[eventType] ?? channelBaseline(channel, eventType);

  function editChannel(channelId: string, eventType: string, value: string): void {
    channelEdits = {
      ...channelEdits,
      [channelId]: { ...channelEdits[channelId], [eventType]: value },
    };
  }

  /** Route every in-app-enabled event to one channel at once — 21 rows is a lot of clicks. */
  function applyAllChannel(channel: Channel, value: string): void {
    const patch: Record<string, string> = {};
    for (const row of rows) {
      if (!row.enabled) continue; // an external channel can't fire without an in-app row
      patch[row.event_type] = value;
    }
    channelEdits = { ...channelEdits, [channel.id]: { ...channelEdits[channel.id], ...patch } };
  }

  function inAppChanged(row: Row): boolean {
    const before = baseline(row.event_type);
    if (!before) return true;
    return (
      before.enabled !== row.enabled ||
      before.digest !== row.digest ||
      Number(before.delay_minutes) !== Number(row.delay_minutes)
    );
  }

  function emailChanged(row: Row): boolean {
    const before = baseline(row.event_type);
    if (!before) return true;
    return (
      before.email_enabled !== row.email_enabled ||
      before.email_digest !== row.email_digest ||
      Number(before.email_delay_minutes) !== Number(row.email_delay_minutes)
    );
  }

  function pushChanged(row: Row): boolean {
    const before = baseline(row.event_type);
    if (!before) return true;
    return (
      before.push_enabled !== row.push_enabled ||
      before.push_digest !== row.push_digest ||
      Number(before.push_delay_minutes) !== Number(row.push_delay_minutes)
    );
  }

  /** A channel's row is written when it already overrides at this scope, or was just changed. */
  const inAppOverride = (row: Row) => row.source === scope || inAppChanged(row);
  const emailOverride = (row: Row) => row.email_source === scope || emailChanged(row);
  const pushOverride = (row: Row) => row.push_source === scope || pushChanged(row);

  /** The API returns "HH:MM:SS"; `TimeInput` speaks "HH:MM". */
  const hhmm = (value: string | null | undefined) => (value ? value.slice(0, 5) : "");

  const generalChanged = $derived(
    Number(general.due_soon_days) !== Number(matrix.general.due_soon_days) ||
      hhmm(general.quiet_hours_start) !== hhmm(matrix.general.quiet_hours_start) ||
      hhmm(general.quiet_hours_end) !== hhmm(matrix.general.quiet_hours_end),
  );
  const generalIsOverride = $derived(matrix.general.source === scope || generalChanged);

  const emailScheduleChanged = $derived(
    hhmm(emailSchedule.digest_time) !== hhmm(matrix.email.digest_time) ||
      (emailSchedule.digest_weekday ?? null) !== (matrix.email.digest_weekday ?? null),
  );
  const emailScheduleIsOverride = $derived(matrix.email.source === scope || emailScheduleChanged);

  const pushScheduleChanged = $derived(
    hhmm(pushSchedule.digest_time) !== hhmm(matrix.push.digest_time) ||
      (pushSchedule.digest_weekday ?? null) !== (matrix.push.digest_weekday ?? null),
  );
  const pushScheduleIsOverride = $derived(matrix.push.source === scope || pushScheduleChanged);

  /** The body the action forwards on. Only the browser knows what "changed" means here. */
  const payload = $derived(
    JSON.stringify({
      events: rows.filter(inAppOverride).map((row) => ({
        event_type: row.event_type,
        enabled: row.enabled,
        delay_minutes: Number(row.delay_minutes) || 0,
        digest: row.digest,
        digest_time: row.digest_time ?? null,
        digest_weekday: row.digest_weekday ?? null,
      })),
      email_events: rows.filter(emailOverride).map((row) => ({
        event_type: row.event_type,
        enabled: row.email_enabled,
        delay_minutes: Number(row.email_delay_minutes) || 0,
        digest: row.email_digest,
      })),
      general: generalIsOverride
        ? {
            due_soon_days: Number(general.due_soon_days),
            quiet_hours_start: hhmm(general.quiet_hours_start) || null,
            quiet_hours_end: hhmm(general.quiet_hours_end) || null,
          }
        : null,
      push_events: rows.filter(pushOverride).map((row) => ({
        event_type: row.event_type,
        enabled: row.push_enabled,
        delay_minutes: Number(row.push_delay_minutes) || 0,
        digest: row.push_digest,
      })),
      email: emailScheduleIsOverride
        ? {
            digest_time: hhmm(emailSchedule.digest_time) || null,
            digest_weekday: emailSchedule.digest_weekday ?? null,
          }
        : null,
      push: pushScheduleIsOverride
        ? {
            digest_time: hhmm(pushSchedule.digest_time) || null,
            digest_weekday: pushSchedule.digest_weekday ?? null,
          }
        : null,
      // Wholesale per channel, and only the events actually routed there: on a channel an
      // absent row *is* "off", so writing the off ones would store 20 rows to say nothing.
      // Every channel is always sent, or the ones left out would be cleared. A route whose
      // in-app row is off is still kept — like the e-mail column, it holds its value and
      // simply cannot fire until the bell is back on.
      channels: channels.map((channel) => ({
        channel_config_id: channel.id,
        events: rows
          .filter((row) => channelValue(channel, row.event_type) !== "off")
          .map((row) => ({
            event_type: row.event_type,
            enabled: true,
            delay_minutes: 0,
            digest: channelValue(channel, row.event_type),
          })),
      })),
    }),
  );

  const overrideCount = $derived(
    rows.filter(inAppOverride).length +
      rows.filter(emailOverride).length +
      rows.filter(pushOverride).length,
  );

  const controlClass =
    "rounded-lg border border-border bg-surface-raised px-2 py-1 text-sm outline-none focus:border-brand disabled:opacity-40";
  const numberClass =
    "w-16 rounded-lg border border-border px-2 py-1 text-sm outline-none focus:border-brand disabled:opacity-40";

  function statusText(source: string, isOverride: boolean): string {
    return isOverride
      ? t("notifications.settings.overridden")
      : t(`notifications.settings.inherited_${source}`);
  }
</script>

{#if error}
  <p class="mb-4 text-sm text-red-600 dark:text-red-400">{t(error)}</p>
{/if}
{#if saved}
  <p class="mb-4 text-sm text-green-600 dark:text-green-400">{t("notifications.settings.saved")}</p>
{/if}

<form method="POST" action="?/save" class="space-y-6" use:enhance={submit}>
  <input type="hidden" name="payload" value={payload} />

  <!-- General: the values that are not per-event. -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="text-sm font-semibold text-text">{t("notifications.settings.general")}</h2>
    <div class="mt-4 grid gap-4 sm:grid-cols-3">
      <label class="block">
        <span class="mb-1 block text-xs font-medium text-text-muted">
          {t("notifications.settings.due_soon_days")}
        </span>
        <input
          type="number"
          min="0"
          max="90"
          value={general.due_soon_days}
          class={numberClass}
          oninput={(e) => (generalEdit = { ...generalEdit, due_soon_days: +e.currentTarget.value })}
        />
        <span class="mt-1 block text-xs text-text-muted">
          {t("notifications.settings.due_soon_hint")}
        </span>
      </label>
      <div>
        <span class="mb-1 block text-xs font-medium text-text-muted">
          {t("notifications.settings.quiet_from")}
        </span>
        <TimeInput
          name="quiet_from"
          value={hhmm(general.quiet_hours_start)}
          onchange={(value) => (generalEdit = { ...generalEdit, quiet_hours_start: value || null })}
        />
      </div>
      <div>
        <span class="mb-1 block text-xs font-medium text-text-muted">
          {t("notifications.settings.quiet_to")}
        </span>
        <TimeInput
          name="quiet_to"
          value={hhmm(general.quiet_hours_end)}
          onchange={(value) => (generalEdit = { ...generalEdit, quiet_hours_end: value || null })}
        />
      </div>
    </div>
    <p class="mt-3 text-xs text-text-muted">{t("notifications.settings.quiet_hint")}</p>
  </section>

  <!-- E-mail digest schedule + bulk set: one global choice for when e-mail digests leave. -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="text-sm font-semibold text-text">{t("notifications.settings.email_schedule")}</h2>
    <p class="mt-1 text-xs text-text-muted">{t("notifications.settings.email_schedule_hint")}</p>
    <div class="mt-4 flex flex-wrap items-end gap-4">
      <div>
        <span class="mb-1 block text-xs font-medium text-text-muted">
          {t("notifications.settings.digest_time")}
        </span>
        <TimeInput
          name="email_digest_time"
          value={hhmm(emailSchedule.digest_time)}
          onchange={(value) => (emailEdit = { ...emailEdit, digest_time: value || null })}
        />
      </div>
      <label class="block">
        <span class="mb-1 block text-xs font-medium text-text-muted">
          {t("notifications.settings.digest_weekday")}
        </span>
        <select
          class={controlClass}
          value={emailSchedule.digest_weekday ?? 0}
          onchange={(e) => (emailEdit = { ...emailEdit, digest_weekday: +e.currentTarget.value })}
        >
          {#each WEEKDAYS as day, i (day)}
            <option value={i}>{day}</option>
          {/each}
        </select>
      </label>
    </div>
    <div class="mt-4">
      <span class="mb-1 block text-xs font-medium text-text-muted">
        {t("notifications.settings.apply_all_email")}
      </span>
      <div class="flex flex-wrap gap-2">
        {#each EMAIL_OPTIONS as option (option)}
          <Button type="button" variant="secondary" size="xs" onclick={() => applyAllEmail(option)}>
            {option === "off"
              ? t("notifications.settings.off")
              : t(`notifications.digest.${option}`)}
          </Button>
        {/each}
      </div>
    </div>
  </section>

  <!-- Browser-push digest schedule: its own, not e-mail's. Someone who wants their mail at 08:00
       and their phone at 09:30 is asking for two ordinary things (#309). -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="text-sm font-semibold text-text">{t("notifications.settings.push_schedule")}</h2>
    <p class="mt-1 text-xs text-text-muted">{t("notifications.settings.push_schedule_hint")}</p>
    <div class="mt-4 flex flex-wrap items-end gap-4">
      <div>
        <span class="mb-1 block text-xs font-medium text-text-muted">
          {t("notifications.settings.digest_time")}
        </span>
        <TimeInput
          name="push_digest_time"
          value={hhmm(pushSchedule.digest_time)}
          onchange={(value) => (pushEdit = { ...pushEdit, digest_time: value || null })}
        />
      </div>
      <label class="block">
        <span class="mb-1 block text-xs font-medium text-text-muted">
          {t("notifications.settings.digest_weekday")}
        </span>
        <select
          class={controlClass}
          value={pushSchedule.digest_weekday ?? 0}
          onchange={(e) => (pushEdit = { ...pushEdit, digest_weekday: +e.currentTarget.value })}
        >
          {#each WEEKDAYS as day, i (day)}
            <option value={i}>{day}</option>
          {/each}
        </select>
      </label>
    </div>
    <div class="mt-4">
      <span class="mb-1 block text-xs font-medium text-text-muted">
        {t("notifications.settings.apply_all_push")}
      </span>
      <div class="flex flex-wrap gap-2">
        {#each EMAIL_OPTIONS as option (option)}
          <Button type="button" variant="secondary" size="xs" onclick={() => applyAllPush(option)}>
            {option === "off"
              ? t("notifications.settings.off")
              : t(`notifications.digest.${option}`)}
          </Button>
        {/each}
      </div>
    </div>
  </section>

  <!-- Per-event delivery: the bell, e-mail, and one column per channel this scope owns. -->
  <section class="overflow-hidden rounded-xl border border-border bg-surface-raised">
    <div class="border-b border-border bg-surface px-4 py-2">
      <h2 class="text-xs font-semibold uppercase tracking-wide text-text-muted">
        {t("notifications.settings.events")}
      </h2>
    </div>
    <div class="overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-border text-left text-xs text-text-muted">
            <th class="px-4 py-2 font-medium" rowspan="2">{t("notifications.settings.event")}</th>
            <th
              class="border-l border-border px-2 py-1 text-center font-semibold uppercase tracking-wide"
              colspan="3"
            >
              {t("notifications.settings.channel_in_app")}
            </th>
            <th
              class="border-l border-border px-2 py-1 text-center font-semibold uppercase tracking-wide"
              colspan="2"
            >
              {t("notifications.settings.channel_email")}
            </th>
            <th
              class="border-l border-border px-2 py-1 text-center font-semibold uppercase tracking-wide"
              colspan="2"
            >
              {t("notifications.settings.channel_push")}
            </th>
            {#each channels as channel (channel.id)}
              <th class="border-l border-border px-2 py-1 text-center font-semibold">
                <span class="block truncate uppercase tracking-wide">{channel.name}</span>
                <span class="block font-normal normal-case text-text-muted">
                  {t(`settings.notifications.kind.${channel.kind}`)}
                </span>
              </th>
            {/each}
            <th class="border-l border-border px-4 py-2 font-medium" rowspan="2">
              {t("notifications.settings.source")}
            </th>
          </tr>
          <tr class="border-b border-border text-left text-xs text-text-muted">
            <th class="border-l border-border px-2 py-1 font-medium">
              {t("notifications.settings.enabled")}
            </th>
            <th class="px-2 py-1 font-medium">{t("notifications.settings.delivery")}</th>
            <th class="px-2 py-1 font-medium">{t("notifications.settings.delay")}</th>
            <th class="border-l border-border px-2 py-1 font-medium">
              {t("notifications.settings.delivery")}
            </th>
            <th class="px-2 py-1 font-medium">{t("notifications.settings.delay")}</th>
            <th class="border-l border-border px-2 py-1 font-medium">
              {t("notifications.settings.delivery")}
            </th>
            <th class="px-2 py-1 font-medium">{t("notifications.settings.delay")}</th>
            {#each channels as channel (channel.id)}
              <th class="border-l border-border px-2 py-1 font-medium">
                <!-- Setting 21 rows one by one is a lot of clicks; this is the same select,
                     applied to the whole column at once. -->
                <select
                  class={controlClass}
                  value=""
                  aria-label={t("notifications.settings.apply_all_channel", {
                    channel: channel.name,
                  })}
                  onchange={(e) => {
                    if (e.currentTarget.value) applyAllChannel(channel, e.currentTarget.value);
                    e.currentTarget.value = "";
                  }}
                >
                  <option value="">{t("notifications.settings.apply_all_short")}</option>
                  {#each EMAIL_OPTIONS as option (option)}
                    <option value={option}>
                      {option === "off"
                        ? t("notifications.settings.off")
                        : t(`notifications.digest.${option}`)}
                    </option>
                  {/each}
                </select>
              </th>
            {/each}
          </tr>
        </thead>
        <tbody class="divide-y divide-border">
          {#each groups as group (group.key)}
            <tr class="bg-surface">
              <th
                colspan={9 + channels.length}
                scope="colgroup"
                class="px-4 py-1.5 text-left text-xs font-semibold uppercase tracking-wide text-text-muted"
              >
                {t(`notifications.group.${group.key}`)}
              </th>
            </tr>
            {#each group.rows as row (row.event_type)}
              <tr>
                <td class="px-4 py-2 text-text">
                  {t(`notifications.event_label.${row.event_type}`)}
                </td>
                <!-- In-app channel. -->
                <td class="border-l border-border px-2 py-2">
                  <input
                    type="checkbox"
                    checked={row.enabled}
                    aria-label={t("notifications.settings.enabled")}
                    onchange={(e) => edit(row.event_type, { enabled: e.currentTarget.checked })}
                  />
                </td>
                <td class="px-2 py-2">
                  <select
                    value={row.digest}
                    class={controlClass}
                    disabled={!row.enabled}
                    aria-label={t("notifications.settings.delivery")}
                    onchange={(e) => edit(row.event_type, { digest: e.currentTarget.value })}
                  >
                    {#each CADENCES as cadence (cadence)}
                      <option value={cadence}>{t(`notifications.digest.${cadence}`)}</option>
                    {/each}
                  </select>
                </td>
                <td class="px-2 py-2">
                  <input
                    type="number"
                    min="0"
                    max="1440"
                    value={row.delay_minutes}
                    class={numberClass}
                    disabled={!row.enabled || row.digest !== "immediate"}
                    aria-label={t("notifications.settings.delay")}
                    oninput={(e) => edit(row.event_type, { delay_minutes: +e.currentTarget.value })}
                  />
                </td>
                <!-- E-mail channel: a subset of in-app, so disabled where in-app is off. -->
                <td class="border-l border-border px-2 py-2">
                  <select
                    value={emailValue(row)}
                    class={controlClass}
                    disabled={!row.enabled}
                    aria-label={t("notifications.settings.channel_email")}
                    onchange={(e) => editEmail(row.event_type, e.currentTarget.value)}
                  >
                    {#each EMAIL_OPTIONS as option (option)}
                      <option value={option}>
                        {option === "off"
                          ? t("notifications.settings.off")
                          : t(`notifications.digest.${option}`)}
                      </option>
                    {/each}
                  </select>
                </td>
                <td class="px-2 py-2">
                  <input
                    type="number"
                    min="0"
                    max="1440"
                    value={row.email_delay_minutes}
                    class={numberClass}
                    disabled={!row.enabled ||
                      !row.email_enabled ||
                      row.email_digest !== "immediate"}
                    aria-label={t("notifications.settings.delay")}
                    oninput={(e) =>
                      edit(row.event_type, { email_delay_minutes: +e.currentTarget.value })}
                  />
                </td>
                <!-- Browser push (#309): the e-mail column's twin, and a subset of in-app too. -->
                <td class="border-l border-border px-2 py-2">
                  <select
                    value={pushValue(row)}
                    class={controlClass}
                    disabled={!row.enabled}
                    aria-label={t("notifications.settings.channel_push")}
                    onchange={(e) => editPush(row.event_type, e.currentTarget.value)}
                  >
                    {#each EMAIL_OPTIONS as option (option)}
                      <option value={option}>
                        {option === "off"
                          ? t("notifications.settings.off")
                          : t(`notifications.digest.${option}`)}
                      </option>
                    {/each}
                  </select>
                </td>
                <td class="px-2 py-2">
                  <input
                    type="number"
                    min="0"
                    max="1440"
                    value={row.push_delay_minutes}
                    class={numberClass}
                    disabled={!row.enabled || !row.push_enabled || row.push_digest !== "immediate"}
                    aria-label={t("notifications.settings.delay")}
                    oninput={(e) =>
                      edit(row.event_type, { push_delay_minutes: +e.currentTarget.value })}
                  />
                </td>
                <!-- One column per external channel: same control, no inheritance. -->
                {#each channels as channel (channel.id)}
                  <td class="border-l border-border px-2 py-2">
                    <select
                      value={channelValue(channel, row.event_type)}
                      class={controlClass}
                      disabled={!row.enabled}
                      aria-label={channel.name}
                      onchange={(e) =>
                        editChannel(channel.id, row.event_type, e.currentTarget.value)}
                    >
                      {#each EMAIL_OPTIONS as option (option)}
                        <option value={option}>
                          {option === "off"
                            ? t("notifications.settings.off")
                            : t(`notifications.digest.${option}`)}
                        </option>
                      {/each}
                    </select>
                  </td>
                {/each}
                <td class="whitespace-nowrap border-l border-border px-4 py-2">
                  <div class="space-y-0.5 text-xs">
                    <div>
                      <span class="text-text-muted"
                        >{t("notifications.settings.channel_in_app")}:</span
                      >
                      {#if inAppOverride(row)}
                        <span class="rounded-full bg-brand/10 px-2 py-0.5 font-medium text-brand">
                          {t("notifications.settings.overridden")}
                        </span>
                      {:else}
                        <span class="text-text-muted">{statusText(row.source, false)}</span>
                      {/if}
                    </div>
                    <div>
                      <span class="text-text-muted"
                        >{t("notifications.settings.channel_email")}:</span
                      >
                      {#if emailOverride(row)}
                        <span class="rounded-full bg-brand/10 px-2 py-0.5 font-medium text-brand">
                          {t("notifications.settings.overridden")}
                        </span>
                      {:else}
                        <span class="text-text-muted">{statusText(row.email_source, false)}</span>
                      {/if}
                    </div>
                    <div>
                      <span class="text-text-muted"
                        >{t("notifications.settings.channel_push")}:</span
                      >
                      {#if pushOverride(row)}
                        <span class="rounded-full bg-brand/10 px-2 py-0.5 font-medium text-brand">
                          {t("notifications.settings.overridden")}
                        </span>
                      {:else}
                        <span class="text-text-muted">{statusText(row.push_source, false)}</span>
                      {/if}
                    </div>
                  </div>
                </td>
              </tr>
            {/each}
          {/each}
        </tbody>
      </table>
    </div>
    <p class="border-t border-border px-4 py-2 text-xs text-text-muted">
      {t("notifications.settings.email_requires_in_app")}
    </p>
  </section>

  <div class="flex flex-wrap items-center justify-between gap-3">
    <p class="text-xs text-text-muted">
      {t("notifications.settings.override_count", { count: overrideCount })}
    </p>
    <div class="flex gap-2">
      <Button
        type="submit"
        variant="secondary"
        formaction="?/reset"
        loading={busy.is("reset")}
        disabled={busy.active}
      >
        {t("notifications.settings.reset")}
      </Button>
      <Button loading={busy.is("save")} disabled={busy.active}>
        {t("common.save")}
      </Button>
    </div>
  </div>
</form>
