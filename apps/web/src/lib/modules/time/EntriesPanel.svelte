<script lang="ts">
  /**
   * "Where did the budget go?" — the entries behind an aggregate, on the page that shows the
   * aggregate (#43, docs/UX.md principle 7: every number opens).
   *
   * Scoped to exactly what the number counted: the host passes the period the aggregate started
   * from, so a monthly project's rows visibly add up to the bar above them. Unapproved hours are
   * **marked, not hidden** — they burn the budget, so dropping them would print a total the bar
   * disagrees with. A running timer is not an entry and never appears (the API filters it out).
   *
   * It shows the last N and **says so when it truncates**: silent truncation reads as "those are
   * all the hours" when they aren't (docs/PERFORMANCE.md). "Alles bekijken" leads to the full
   * report, pre-filtered to the same project and the same period — never as the only way in,
   * because Overzicht is manager-only and the person asking usually isn't one.
   *
   * **Host contract:** the page this renders on must expose `?/updateEntry` and `?/deleteEntry`
   * form actions (SvelteKit actions live on the page, so the host owns them). Mirrors the Uren
   * report's, which post the same fields.
   */
  import { Pencil, Trash2 } from "@lucide/svelte";

  import { page } from "$app/state";
  import { fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import type { EntityPanelContext, EntityPanelLookups } from "$lib/core/registry";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";

  import EntryForm from "./EntryForm.svelte";
  import TimeEntryRow from "./TimeEntryRow.svelte";

  interface Entry {
    id: string;
    user_id: string;
    started_at: string;
    ended_at?: string | null;
    minutes: number;
    break_minutes?: number;
    billable?: boolean;
    description?: string | null;
    company_id?: string | null;
    project_id?: string | null;
    task_id?: string | null;
    approved_at?: string | null;
    invoiced_at?: string | null;
  }

  /** What `load` returns. `total` is the API's count for the period, not `entries.length`. */
  interface PanelData {
    entries: Entry[];
    total: number;
    viewAllHref: string;
  }

  let {
    data,
    context,
    lookups,
  }: {
    data: unknown;
    context: EntityPanelContext;
    lookups: EntityPanelLookups;
  } = $props();

  const panel = $derived(
    (data ?? { entries: [], total: 0, viewAllHref: "/overview" }) as PanelData,
  );
  const truncated = $derived(panel.total > panel.entries.length);

  // Overzicht is gated on `time.report.read`: its layout redirects anyone else to the dashboard.
  // Offering a member a link that bounces them is worse than offering none — which is the whole
  // reason this panel exists (#43). They still get told when the list truncated.
  const canViewReport = $derived(can(page.data.user, "time.report.read"));

  const memberName = (id: string) => {
    const member = lookups.members.find((m) => m.user_id === id);
    return member ? member.full_name || member.email : "";
  };
  const companyName = (id?: string | null) =>
    lookups.companies.find((c) => c.id === id)?.name ?? "";
  const taskTitle = (id?: string | null) => lookups.tasks.find((tk) => tk.id === id)?.title ?? "";

  // On a project's own panel the project is a given; naming it in every row would be noise.
  const entryLabel = (entry: Entry) =>
    [companyName(entry.company_id), taskTitle(entry.task_id)].filter(Boolean).join(" · ");

  let editingEntry = $state<Entry | null>(null);
  let showEdit = $state(false);
  let deleteId = $state("");
  let confirmDelete = $state(false);
</script>

<div class="mb-3 flex flex-wrap items-baseline justify-between gap-2">
  <p class="text-sm text-text-muted">
    {#if context.periodStart}
      {t("time.panel.since", { date: fmtNumericDate(context.periodStart) })}
    {:else}
      {t("time.panel.all_time")}
    {/if}
  </p>
  <!-- The count, not a total: the hours live in the bar above, and a total summed from the rows
       this panel happens to hold would be the total of the page, quietly disagreeing with it. -->
  <p class="text-sm font-medium tabular-nums text-text">
    {t("time.panel.entry_count", { count: panel.total })}
  </p>
</div>

{#if panel.entries.length === 0}
  <p class="py-4 text-sm text-text-muted">{t("time.panel.empty")}</p>
{:else}
  <ul class="divide-y divide-border">
    {#each panel.entries as entry (entry.id)}
      <li class="flex items-center gap-2 py-2.5">
        <div class="min-w-0 flex-1">
          <TimeEntryRow {entry} label={entryLabel(entry)} employee={memberName(entry.user_id)} />
        </div>
        <!-- A list of records is never read-only because it sits on another page (docs/UX.md).
             The API still enforces the role and approval-lock rules behind these. -->
        <ActionsMenu
          compact
          items={[
            {
              label: t("common.edit"),
              icon: Pencil,
              onclick: () => {
                editingEntry = entry;
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
          ]}
        />
      </li>
    {/each}
  </ul>
{/if}

{#if truncated || canViewReport}
  <div class="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3">
    {#if truncated}
      <!-- It truncated, so it says so. -->
      <p class="text-xs text-text-muted">
        {t("time.panel.truncated", { shown: panel.entries.length, total: panel.total })}
      </p>
    {:else}
      <span></span>
    {/if}
    {#if canViewReport}
      <a
        href={panel.viewAllHref}
        data-sveltekit-preload-data="hover"
        class="text-sm font-medium text-brand hover:underline">{t("time.panel.view_all")}</a
      >
    {/if}
  </div>
{/if}

<Modal bind:open={showEdit} title={t("time.edit_entry")}>
  {#if editingEntry}
    {#key editingEntry.id}
      <EntryForm
        action="?/updateEntry"
        deleteAction="?/deleteEntry"
        entry={editingEntry}
        date={editingEntry.started_at.slice(0, 10)}
        companies={lookups.companies}
        projects={lookups.projects}
        tasks={lookups.tasks}
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
