<script lang="ts">
  /**
   * The new-task dialog: **the** way a task is made (#391). It began behind a picker's
   * "＋ … toevoegen" (docs/UX.md) and is now what `Nieuwe taak` opens too — on `/tasks`, on a
   * client's Taken panel, on the client header and on a project's to-do list — because the one
   * entry point that skipped it posted a row before the user had been asked anything, and an
   * abandoned create is a task on somebody's board (#350's `unnamed` mitigated the display of
   * that, never the row).
   *
   * Real fields — title, due date, the employees on it — prefilled with what was typed, posting
   * to the caller's own action: a picker's answers with `inlineCreated` so it auto-selects the
   * new task, the list's redirects to the task in edit mode. The company/project ride along
   * hidden when the caller has them pinned (e.g. the approve dialog's current picks).
   *
   * The roster is the same `AssigneePicker` the full task form draws (#375), not a single
   * Combobox. A task created from a pending email is routinely work for two people, and a
   * dialog that can only name one made "assign the pair" a second visit to the task itself —
   * which is exactly the trip the inline-create exists to save. It posts the whole roster in
   * one hidden field, so the caller's action forwards `assignees` and never a lone id.
   */
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import AssigneePicker from "$lib/core/ui/AssigneePicker.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";

  let {
    open = $bindable(false),
    title = "",
    companyId = null,
    projectId = null,
    members = [],
    assignees = [],
    action = "?/createTask",
    error = null,
    pickerSlot = "task",
  }: {
    open?: boolean;
    /** What was typed in the picker. */
    title?: string;
    companyId?: string | null;
    projectId?: string | null;
    // `email` is nullable on `/members/lookup` and on `AssigneePicker`, which is what this
    // forwards them to; narrowing it here only made every caller cast.
    members?: {
      user_id: string;
      full_name: string | null;
      email: string | null;
      is_active?: boolean;
    }[];
    /**
     * The roster the picker opens with. `Nieuwe taak` used to assign its creator behind the
     * scenes (#391); it still offers to, as a chip that is visible and removable rather than a
     * decision taken off screen. Empty is the honest default everywhere the surface never
     * assigned anyone.
     */
    assignees?: { user_id: string; is_primary: boolean }[];
    action?: string;
    /** The page's `form?.qcError`. */
    error?: string | null;
    /** Echoed in `inlineCreated` so only the picker that asked auto-selects. */
    pickerSlot?: string;
  } = $props();

  const busy = new InFlight();

  /**
   * The refusal this very submit came back with.
   *
   * `error` (the page's `form?.qcError`) only reaches a dialog whose action lives on the page it
   * is drawn on: SvelteKit applies a *failure* result to `page.form` only for a same-path
   * action, and three of the four callers post to `/tasks?/create` from somewhere else. Reading
   * the result here works for all of them, and a dialog that stays open saying nothing is the
   * worst of the available answers.
   */
  let refusal = $state<string | null>(null);

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<Modal bind:open title={t("tasks.new")}>
  {#key title + String(open)}
    <form
      method="POST"
      {action}
      use:enhance={busy.wrap("", () => {
        refusal = null;
        return ({ result, update }) => {
          // A redirect closes it too: the list's create lands on the new task in edit mode
          // (#391), and a dialog left open would flash over the page being navigated to.
          if (result.type === "success" || result.type === "redirect") open = false;
          else if (result.type === "failure") {
            const data = (result.data ?? {}) as { qcError?: string; error?: string };
            refusal = data.qcError ?? data.error ?? "errors.validation";
          }
          void update({ reset: false });
        };
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
      <div>
        <span class="mb-1 block text-sm font-medium text-text">{t("tasks.field.due_date")}</span>
        <DateInput name="due_date" id="qc-task-due" />
      </div>
      <!-- Guarded on the roster, not on the opening list: a picker whose every option sits
           behind the search is still a picker, and hiding it would take the search with it. -->
      {#if members.length > 0}
        <div>
          <span class="mb-1 block text-sm font-medium text-text">{t("tasks.field.assignees")}</span>
          <AssigneePicker {members} value={assignees} id="qc-task-assignees" />
        </div>
      {/if}
      {#if refusal ?? error}
        <p class="text-sm text-red-600 dark:text-red-400">{t((refusal ?? error)!)}</p>
      {/if}
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
