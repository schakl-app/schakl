<script lang="ts">
  /**
   * Shared time-entry row: date + time, who logged it, what it was booked to, the duration and
   * the sign-off status. One row per concept (docs/UX.md), so an entry reads the same in the
   * Uren report's mobile view and in the Uren panel under a project's budget bar (#42, #43).
   *
   * Purely presentational: the ⋯ menu that edits and deletes the entry belongs to the host, which
   * is the surface that owns the form actions.
   */
  import { fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";

  import EntryStatusPill from "./EntryStatusPill.svelte";
  import { formatMinutes, formatTime } from "./format";

  interface Entry {
    id: string;
    started_at: string;
    minutes: number;
    billable?: boolean;
    description?: string | null;
    approved_at?: string | null;
    invoiced_at?: string | null;
  }

  let {
    entry,
    label = "",
    employee = "",
  }: {
    entry: Entry;
    /** What the entry was booked to — "Client · Project · Task", already composed by the host. */
    label?: string;
    /** The employee's display name. Empty on a surface that shows one person's own hours. */
    employee?: string;
  } = $props();
</script>

<div class="flex items-center gap-3">
  <div class="min-w-0 flex-1">
    <p class="flex flex-wrap items-baseline gap-x-2 text-sm">
      <span class="font-medium text-text">{label || t("time.general")}</span>
      {#if employee}<span class="text-text-muted">{employee}</span>{/if}
    </p>
    <p class="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-text-muted">
      <span class="tabular-nums">
        {fmtNumericDate(entry.started_at.slice(0, 10))}
        {formatTime(entry.started_at)}
      </span>
      {#if entry.description}<span class="truncate">{entry.description}</span>{/if}
    </p>
  </div>
  <div class="flex shrink-0 items-center gap-2">
    <span class="text-sm font-semibold tabular-nums text-text">{formatMinutes(entry.minutes)}</span>
    <EntryStatusPill {entry} />
  </div>
</div>
