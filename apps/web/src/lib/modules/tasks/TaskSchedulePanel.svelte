<script lang="ts">
  /**
   * Planned blocks for one task on its detail page (#188): schedule it, edit/move a block, and —
   * once a block's time has passed — log the worked hours from it in one click (confirm-to-log,
   * the owner's choice). Every mutation posts to the host page's schedule actions.
   */
  import { CalendarClock, Pencil, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { fmtDayMonth } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import { formatMinutes } from "$lib/modules/time/format";

  import { durationMinutes, localDayTime } from "./schedule";
  import ScheduleTaskModal from "./ScheduleTaskModal.svelte";

  interface Block {
    id: string;
    user_id: string | null;
    user_name?: string | null;
    starts_at: string;
    ends_at: string;
    start: string;
    note: string | null;
    time_entry_id: string | null;
  }
  interface TaskRef {
    id: string;
    title: string;
    project_id?: string | null;
    company_id?: string | null;
    assignee_user_id?: string | null;
    allocated_minutes?: number | null;
    due_date?: string | null;
  }
  interface Member {
    user_id: string;
    full_name: string | null;
    email: string;
  }

  let {
    schedules = [],
    task,
    members = [],
    currentUserId,
    canWrite = false,
    canScheduleAny = false,
  }: {
    schedules?: Block[];
    task: TaskRef;
    members?: Member[];
    currentUserId: string;
    canWrite?: boolean;
    canScheduleAny?: boolean;
  } = $props();

  const blocks = $derived([...schedules].sort((a, b) => a.starts_at.localeCompare(b.starts_at)));

  let modalOpen = $state(false);
  let editingBlock = $state<Block | null>(null);
  let logOpen = $state(false);
  let logBlock = $state<Block | null>(null);
  let logMinutes = $state(0);
  let logDescription = $state("");
  let logBillable = $state(true);
  let deleteOpen = $state(false);
  let deleteId = $state("");

  const busy = new InFlight();

  function timeRange(block: Block): string {
    return `${localDayTime(block.starts_at).time}–${localDayTime(block.ends_at).time}`;
  }
  function canEditBlock(block: Block): boolean {
    return block.user_id === currentUserId ? canWrite : canScheduleAny;
  }
  function isLoggable(block: Block): boolean {
    return (
      block.user_id === currentUserId &&
      !block.time_entry_id &&
      new Date(block.ends_at).getTime() < Date.now()
    );
  }

  function openCreate() {
    editingBlock = null;
    modalOpen = true;
  }
  function openEdit(block: Block) {
    editingBlock = block;
    modalOpen = true;
  }
  function openDelete(block: Block) {
    deleteId = block.id;
    deleteOpen = true;
  }
  function openLog(block: Block) {
    logBlock = block;
    logMinutes = durationMinutes(block.starts_at, block.ends_at);
    logDescription = task.title;
    logBillable = true;
    logOpen = true;
  }
</script>

{#if blocks.length > 0 || canWrite}
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <div class="mb-3 flex items-center justify-between gap-2">
      <h3 class="text-xs font-semibold uppercase tracking-wide text-text-muted">
        {t("tasks.schedule.panel_title")}
      </h3>
      {#if canWrite}
        <button
          type="button"
          class="flex items-center gap-1 rounded-lg border border-border px-2 py-1 text-xs text-text hover:border-brand hover:text-brand"
          onclick={openCreate}
        >
          <CalendarClock size={14} />
          {t("tasks.schedule.action")}
        </button>
      {/if}
    </div>

    {#if blocks.length === 0}
      <p class="text-sm text-text-muted">{t("tasks.schedule.panel_empty")}</p>
    {:else}
      <ul class="space-y-2">
        {#each blocks as block (block.id)}
          <li
            class="flex items-center justify-between gap-2 rounded-lg border border-border px-3 py-2"
          >
            <div class="min-w-0">
              <p class="text-sm text-text">{fmtDayMonth(block.start)} · {timeRange(block)}</p>
              <p class="flex flex-wrap items-center gap-x-2 text-xs text-text-muted">
                {#if block.user_id && block.user_id !== currentUserId}
                  <span>{block.user_name}</span>
                {/if}
                <span>{formatMinutes(durationMinutes(block.starts_at, block.ends_at))}</span>
                {#if block.time_entry_id}
                  <span class="text-emerald-600 dark:text-emerald-400"
                    >· {t("tasks.schedule.logged")}</span
                  >
                {/if}
              </p>
            </div>
            <div class="flex shrink-0 items-center gap-1">
              {#if isLoggable(block)}
                <button
                  type="button"
                  class="rounded-lg border border-border px-2 py-1 text-xs text-text hover:border-brand hover:text-brand"
                  onclick={() => openLog(block)}
                >
                  {t("tasks.schedule.log_time")}
                </button>
              {/if}
              {#if canEditBlock(block)}
                <ActionsMenu
                  compact
                  items={[
                    { label: t("common.edit"), icon: Pencil, onclick: () => openEdit(block) },
                    {
                      label: t("common.delete"),
                      icon: Trash2,
                      danger: true,
                      onclick: () => openDelete(block),
                    },
                  ]}
                />
              {/if}
            </div>
          </li>
        {/each}
      </ul>
    {/if}
  </section>

  <!-- Create / edit block (the task is fixed here — no picker). -->
  <ScheduleTaskModal
    bind:open={modalOpen}
    {task}
    editBlock={editingBlock}
    {members}
    {currentUserId}
    {canScheduleAny}
    action={editingBlock ? "?/updateSchedule" : "?/scheduleTask"}
  />

  <!-- Confirm-to-log a passed block: everything defaults from the block, adjust and save. -->
  <Modal bind:open={logOpen} title={t("tasks.schedule.log_title")}>
    <form
      method="POST"
      action="?/logScheduleTime"
      use:enhance={busy.wrap("", () => {
        return async ({ result, update }) => {
          if (result.type === "success") {
            logOpen = false;
            await update();
          } else {
            await update({ reset: false });
          }
        };
      })}
      class="space-y-4"
    >
      <input type="hidden" name="schedule_id" value={logBlock?.id ?? ""} />
      <div>
        <label for="log-minutes" class="mb-1 block text-sm font-medium text-text">
          {t("tasks.schedule.worked_minutes")}
        </label>
        <input
          id="log-minutes"
          name="minutes"
          type="number"
          min="1"
          max="1440"
          step="15"
          bind:value={logMinutes}
          class="w-full min-w-0 rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text"
        />
      </div>
      <div>
        <label for="log-desc" class="mb-1 block text-sm font-medium text-text">
          {t("time.field.description")}
        </label>
        <input
          id="log-desc"
          name="description"
          bind:value={logDescription}
          class="w-full min-w-0 rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text"
        />
      </div>
      <label class="flex items-center gap-2 text-sm text-text">
        <input type="checkbox" name="billable" value="true" bind:checked={logBillable} />
        {t("time.billable")}
      </label>
      {#if !logBillable}
        <input type="hidden" name="billable" value="false" />
      {/if}
      <div class="flex justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-3 py-2 text-sm text-text hover:bg-surface"
          onclick={() => (logOpen = false)}>{t("common.cancel")}</button
        >
        <Button type="submit" loading={busy.active}>
          {t("tasks.schedule.log_submit")}
        </Button>
      </div>
    </form>
  </Modal>

  <ConfirmDialog
    bind:open={deleteOpen}
    title={t("tasks.schedule.delete_title")}
    message={t("tasks.schedule.delete_confirm")}
    action="?/deleteSchedule"
    fields={{ schedule_id: deleteId }}
  />
{/if}
