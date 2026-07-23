<script lang="ts">
  /**
   * The delivery matrix (issue #16) — one surface, one save button (docs/UX.md).
   *
   * Preferences resolve default ← org row ← user row, **whole rows at a time**. A save must
   * therefore not quietly turn every inherited row into an override, or after one click nothing
   * would inherit again and tomorrow's better default could never reach anyone. So this posts
   * only the rows that already override at this scope, plus the rows actually changed here.
   * Everything else keeps falling through, and the badge beside it says where its value came from.
   *
   * Edits live in a sparse `edits` map layered over the loaded matrix, rather than a copy of it:
   * changing a switch and changing it back leaves the row inherited instead of freezing today's
   * default into a row of its own.
   *
   * "Reset" posts an empty set, which deletes this scope's rows — the API's own meaning of reset.
   *
   * Deliberately not exposed yet: a digest's time-of-day and weekday. The columns exist and the
   * API honours them; left unset they fall back to 08:00 Europe/Amsterdam, on Monday.
   */
  import { enhance } from "$app/forms";
  import type { SubmitFunction } from "@sveltejs/kit";

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
  }
  interface General {
    due_soon_days: number;
    quiet_hours_start?: string | null;
    quiet_hours_end?: string | null;
    source: string;
  }

  let {
    matrix,
    /** Which layer this screen writes: a user's own row, or the org's default row. */
    scope,
    error = null,
    saved = false,
  }: {
    matrix: { events: Row[]; general: General };
    scope: "user" | "org";
    error?: string | null;
    saved?: boolean;
  } = $props();

  const CADENCES = ["immediate", "hourly", "daily", "weekly"] as const;

  let edits = $state<Record<string, Partial<Row>>>({});
  let generalEdit = $state<Partial<General>>({});

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

  function baseline(eventType: string): Row | undefined {
    return matrix.events.find((row) => row.event_type === eventType);
  }

  function edit(eventType: string, patch: Partial<Row>): void {
    edits = { ...edits, [eventType]: { ...edits[eventType], ...patch } };
  }

  function isChanged(row: Row): boolean {
    const before = baseline(row.event_type);
    if (!before) return true;
    return (
      before.enabled !== row.enabled ||
      before.digest !== row.digest ||
      Number(before.delay_minutes) !== Number(row.delay_minutes)
    );
  }

  /** A row is written when it already overrides at this scope, or was just changed here. */
  function isOverride(row: Row): boolean {
    return row.source === scope || isChanged(row);
  }

  /** The API returns "HH:MM:SS"; `TimeInput` speaks "HH:MM". */
  const hhmm = (value: string | null | undefined) => (value ? value.slice(0, 5) : "");

  const generalChanged = $derived(
    Number(general.due_soon_days) !== Number(matrix.general.due_soon_days) ||
      hhmm(general.quiet_hours_start) !== hhmm(matrix.general.quiet_hours_start) ||
      hhmm(general.quiet_hours_end) !== hhmm(matrix.general.quiet_hours_end),
  );
  const generalIsOverride = $derived(matrix.general.source === scope || generalChanged);

  /** The body the action forwards on. Only the browser knows what "changed" means here. */
  const payload = $derived(
    JSON.stringify({
      events: rows.filter(isOverride).map((row) => ({
        event_type: row.event_type,
        enabled: row.enabled,
        delay_minutes: Number(row.delay_minutes) || 0,
        digest: row.digest,
        digest_time: row.digest_time ?? null,
        digest_weekday: row.digest_weekday ?? null,
      })),
      general: generalIsOverride
        ? {
            due_soon_days: Number(general.due_soon_days),
            quiet_hours_start: hhmm(general.quiet_hours_start) || null,
            quiet_hours_end: hhmm(general.quiet_hours_end) || null,
          }
        : null,
    }),
  );

  const overrideCount = $derived(rows.filter(isOverride).length);

  const controlClass =
    "rounded-lg border border-border bg-surface-raised px-2 py-1 text-sm outline-none focus:border-brand";
  const numberClass =
    "w-20 rounded-lg border border-border px-2 py-1 text-sm outline-none focus:border-brand disabled:opacity-40";
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

  <!-- Per-event delivery. -->
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
            <th class="px-4 py-2 font-medium">{t("notifications.settings.event")}</th>
            <th class="px-2 py-2 font-medium">{t("notifications.settings.enabled")}</th>
            <th class="px-2 py-2 font-medium">{t("notifications.settings.delivery")}</th>
            <th class="px-2 py-2 font-medium">{t("notifications.settings.delay")}</th>
            <th class="px-4 py-2 font-medium">{t("notifications.settings.source")}</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-border">
          {#each groups as group (group.key)}
            <tr class="bg-surface">
              <th
                colspan="5"
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
                <td class="px-2 py-2">
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
                <td class="whitespace-nowrap px-4 py-2">
                  {#if isOverride(row)}
                    <span
                      class="rounded-full bg-brand/10 px-2 py-0.5 text-xs font-medium text-brand"
                    >
                      {t("notifications.settings.overridden")}
                    </span>
                  {:else}
                    <span class="text-xs text-text-muted">
                      {t(`notifications.settings.inherited_${row.source}`)}
                    </span>
                  {/if}
                </td>
              </tr>
            {/each}
          {/each}
        </tbody>
      </table>
    </div>
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
