<script lang="ts">
  /**
   * Company-detail panel: the hours logged against this client (CLAUDE.md §6).
   *
   * It used to be a description and a duration per row (#400), which on a real client rendered
   * *"Back-up teruggezet op de testomgeving"* three times — three days, three colleagues, three
   * indistinguishable lines — on the screen somebody reads while that client is on the phone.
   * Four facts now, in the team's own order: **when**, **who**, **what**, **how long**.
   *
   * Three rules came out of fixing it and none of them is about time registration.
   *
   * - **The date was already over the wire.** `started_at` was in the payload and declared in
   *   this component's own interface, and never drawn. So the fix costs nothing to fetch, and
   *   once there is a date to group by, ten rows across four days read as four days of work
   *   rather than as a list.
   * - **A panel that truncates says so.** Ten rows under a total of 77 hours read as "that is
   *   all of them"; the count line and *Alle uren* are the way through it (docs/UX.md principle
   *   7, docs/PERFORMANCE.md). The link is gated on `time.report.read` because that is what the
   *   Uren-overzicht demands, and a link that always bounces is a broken control (#253) — a
   *   member still gets told the list was cut.
   * - **A list of records is never read-only because it sits on another page.** The ⋯ corrects
   *   the row here. It mirrors the API's *own* refinement rather than inventing one: your own
   *   hours need `time.entry.write:own`, a colleague's needs `:any`, and an approved entry is
   *   locked to whoever may approve it — the service's `_ensure_writable` / `_ensure_not_locked`
   *   in three lines of `$derived`.
   *
   * **Host contract:** the page must expose `?/updateEntry`, `?/deleteEntry` and (for the ＋,
   * #402) `?/createEntry` form actions, and `updateEntry` must post **only the fields it was
   * given** — see `EntryQuickEdit`. All three are `timeEntryActions`, spread by the host.
   */
  import { Check, Pencil, Trash2 } from "@lucide/svelte";

  import { page } from "$app/state";
  import { capitalizeFirst, fmtPeriod, fmtWeekdayShort } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import type { PanelMember } from "$lib/core/registry";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import PanelHeader from "$lib/core/ui/PanelHeader.svelte";
  import PanelRows from "$lib/core/ui/PanelRows.svelte";
  import PersonChip from "$lib/core/ui/PersonChip.svelte";

  import EntryQuickEdit from "./EntryQuickEdit.svelte";
  import LogTimeDialog from "./LogTimeDialog.svelte";
  import { formatMinutes } from "./format";

  let {
    companyId,
    data,
    members = [],
    title = "",
  }: {
    companyId: string;
    data: Record<string, unknown>;
    members?: PanelMember[];
    title?: string;
  } = $props();

  interface RecentEntry {
    id: string;
    user_id: string;
    description: string | null;
    minutes: number;
    started_at: string;
    ended_at: string | null;
    break_minutes: number;
    billable: boolean;
    approved_at: string | null;
  }

  //: Three days of work is what the card is for; the rest is one click of the expander. The
  //: feed default's browser-side companion (#407).
  const COLLAPSED = 4;

  const totalMinutes = $derived((data.total_minutes ?? 0) as number);
  const recent = $derived((data.recent ?? []) as RecentEntry[]);
  /** How many exist behind the ten. Absent on a payload that predates #400 — then the rows we
   *  hold are all we can honestly claim, so the notice simply does not appear. */
  const totalEntries = $derived((data.total_entries ?? recent.length) as number);

  // Entry times are wall-clock stored as UTC (`format.ts`), so the day is sliced off the ISO
  // string rather than parsed through a zone — the same read `TimeEntryRow` and `EntryForm` do.
  function groupByDay(rows: RecentEntry[]) {
    const groups: { day: string; entries: RecentEntry[] }[] = [];
    for (const entry of rows) {
      const day = entry.started_at.slice(0, 10);
      const last = groups.at(-1);
      if (last?.day === day) last.entries.push(entry);
      else groups.push({ day, entries: [entry] });
    }
    return groups;
  }

  const me = $derived(page.data.user?.id ?? null);
  const canWrite = $derived(can(page.data.user, "time.entry.write"));
  // Overzicht redirects anyone without `time.report.read`; offering them a link that bounces is
  // worse than offering none (the Uren panel on a project settled this at #43).
  const canViewReport = $derived(can(page.data.user, "time.report.read"));
  const canApprove = $derived(can(page.data.user, "time.entry.approve"));

  /** The API's own rule, mirrored: `:own` for your row, `:any` for a colleague's, and an
   *  approved entry only for whoever may approve. The API enforces all three, harder. */
  const mayEdit = (entry: RecentEntry) =>
    (entry.approved_at === null || canApprove) &&
    (entry.user_id === me
      ? can(page.data.user, "time.entry.write", "own")
      : can(page.data.user, "time.entry.write", "any"));

  const memberOf = (userId: string) => members.find((m) => m.user_id === userId);

  let logging = $state(false);
  let editing = $state<RecentEntry | null>(null);
  let showEdit = $state(false);
  let deleteId = $state("");
  let confirmDelete = $state(false);

  function menuItems(entry: RecentEntry) {
    return [
      {
        label: t("common.edit"),
        icon: Pencil,
        onclick: () => {
          editing = entry;
          showEdit = true;
        },
      },
      {
        label: t("common.delete"),
        icon: Trash2,
        danger: true,
        onclick: () => {
          deleteId = entry.id;
          confirmDelete = true;
        },
      },
    ];
  }
</script>

<!-- The way through lives on the footer line with the count it belongs to (#407); it used to
     sit up here, so a truncated panel said "8 van 57" in one corner and "Alles bekijken" in the
     other — one fact, two places, and neither naming the other. -->
<PanelHeader {title} />

<p class="text-sm text-text">
  {t("time.total_logged")}:
  <span class="font-semibold text-text">{formatMinutes(totalMinutes)}</span>
</p>
<!-- Collapsed to a working handful, expandable in place, and honest about the rest (#407).
     The hand-over is offered only to somebody `/overview` will let in — it redirects anyone
     without `time.report.read`, and a link that always bounces is a broken control (#253) — so
     a member without it gets the count and no link. -->
<PanelRows
  rows={recent}
  collapsed={COLLAPSED}
  total={totalEntries}
  href={canViewReport ? `/overview?company_id=${companyId}` : undefined}
  linkLabel={t("time.panel.view_all_count", { count: totalEntries })}
  alwaysLink={canViewReport}
>
  {#snippet children(shown)}
    {#each groupByDay(shown) as group (group.day)}
      <p class="mt-3 text-xs font-semibold text-text-muted">
        {capitalizeFirst(fmtWeekdayShort(group.day))}
        {fmtPeriod(group.day)}
      </p>
      <ul class="divide-y divide-border">
        {#each group.entries as entry (entry.id)}
          <li class="flex items-start gap-2 py-2 text-sm">
            <div class="min-w-0 flex-1">
              <p class="break-words text-text">{entry.description ?? t("time.general")}</p>
              <!-- Who did it, as the chip every other "who did this" on the platform uses — a name
               beside a face, never a bare initials disc (`PersonChip`). -->
              <span class="mt-0.5 flex items-center gap-2 text-xs">
                <PersonChip
                  size="xs"
                  muted
                  name={memberOf(entry.user_id)?.full_name ?? null}
                  email={memberOf(entry.user_id)?.email ?? null}
                  avatarUrl={memberOf(entry.user_id)?.avatar_url ?? null}
                  label={memberOf(entry.user_id) ? null : t("assignees.unknown")}
                />
                <!-- Whether we bill for it, as a quiet marker rather than a fourth column: the same
                 ✓ / — the Uren-overzicht draws, so one hour reads the same in both places. The
                 state rides the glyph as well as the colour (a tenant's brand may be green). -->
                {#if entry.billable}
                  <span
                    class="inline-flex shrink-0 text-green-600 dark:text-green-400"
                    title={t("time.billable")}
                  >
                    <Check size={13} aria-hidden="true" />
                    <span class="sr-only">{t("time.billable")}</span>
                  </span>
                {:else}
                  <span class="shrink-0 text-text-muted" title={t("time.not_billable")}>
                    &mdash;<span class="sr-only">{t("time.not_billable")}</span>
                  </span>
                {/if}
              </span>
            </div>
            <span class="shrink-0 font-semibold tabular-nums text-text">
              {formatMinutes(entry.minutes)}
            </span>
            {#if mayEdit(entry)}
              <ActionsMenu compact items={menuItems(entry)} />
            {/if}
          </li>
        {/each}
      </ul>
    {/each}
  {/snippet}
  {#snippet actions()}
    {#if canWrite}
      <!-- Log hours from where the client is, and *stay* there (#402). This used to be a link
           to `/time?company=…` — the deep link was right and the trip was one-way, which is
           the one thing every other "record something about this client" on this page does
           not do. -->
      <button type="button" onclick={() => (logging = true)} class="text-brand hover:underline">
        ＋ {t("time.log_for_client")}
      </button>
    {/if}
  {/snippet}
</PanelRows>

{#if canWrite}
  <LogTimeDialog bind:open={logging} {companyId} />
{/if}

<Modal bind:open={showEdit} title={t("time.edit_entry")}>
  {#if editing}
    {#key editing.id}
      <EntryQuickEdit
        entry={editing}
        oncancel={() => (showEdit = false)}
        ondone={() => (showEdit = false)}
      />
    {/key}
  {/if}
</Modal>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("time.delete")}
  message={t("time.delete_confirm")}
  action="?/deleteEntry"
  fields={{ id: deleteId }}
/>
