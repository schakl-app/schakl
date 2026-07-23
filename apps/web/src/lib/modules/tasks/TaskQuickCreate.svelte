<script lang="ts">
  /**
   * The new-task dialog behind a picker's "＋ … toevoegen" (docs/UX.md): real fields —
   * title, due date, assignee — prefilled with what was typed, posting to the caller's
   * `createTask`-style action, which reports back via `inlineCreated` so the asking picker
   * auto-selects the new task. The company/project ride along hidden when the caller has
   * them pinned (e.g. the approve dialog's current picks).
   */
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";

  let {
    open = $bindable(false),
    title = "",
    companyId = null,
    projectId = null,
    members = [],
    action = "?/createTask",
    error = null,
    pickerSlot = "task",
  }: {
    open?: boolean;
    /** What was typed in the picker. */
    title?: string;
    companyId?: string | null;
    projectId?: string | null;
    members?: { user_id: string; full_name: string | null; email: string }[];
    action?: string;
    /** The page's `form?.qcError`. */
    error?: string | null;
    /** Echoed in `inlineCreated` so only the picker that asked auto-selects. */
    pickerSlot?: string;
  } = $props();

  const memberOptions = $derived(
    members.map((m) => ({ value: m.user_id, label: m.full_name || m.email })),
  );
  let assigneeId = $state("");

  const busy = new InFlight();

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<Modal bind:open title={t("tasks.new")}>
  {#key title + String(open)}
    <form
      method="POST"
      {action}
      use:enhance={busy.wrap("", () => ({ result, update }) => {
        if (result.type === "success") open = false;
        void update({ reset: false });
      })}
      class="space-y-3"
    >
      <input type="hidden" name="slot" value={pickerSlot} />
      {#if companyId}<input type="hidden" name="company_id" value={companyId} />{/if}
      {#if projectId}<input type="hidden" name="project_id" value={projectId} />{/if}
      <div>
        <label for="qc-task-title" class="mb-1 block text-sm font-medium text-text"
          >{t("tasks.field.title")}</label
        >
        <input id="qc-task-title" name="title" value={title} required class={inputClass} />
      </div>
      <div class="grid gap-3 sm:grid-cols-2">
        <div>
          <span class="mb-1 block text-sm font-medium text-text">{t("tasks.field.due_date")}</span>
          <DateInput name="due_date" id="qc-task-due" />
        </div>
        {#if memberOptions.length > 0}
          <div>
            <span class="mb-1 block text-sm font-medium text-text">{t("tasks.field.assignee")}</span
            >
            <Combobox
              items={memberOptions}
              name="assignee_user_id"
              value={assigneeId}
              placeholder={t("common.none")}
              onselect={(v) => (assigneeId = v)}
              id="qc-task-assignee"
            />
          </div>
        {/if}
      </div>
      {#if error}<p class="text-sm text-red-600 dark:text-red-400">{t(error)}</p>{/if}
      <div class="flex justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm"
          onclick={() => (open = false)}>{t("common.cancel")}</button
        >
        <Button loading={busy.active}>{t("common.create")}</Button>
      </div>
    </form>
  {/key}
</Modal>
