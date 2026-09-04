<script lang="ts">
  /**
   * One field of a record, edited in place — the shape `InlineText` (#455) gave free text,
   * generalised to any control.
   *
   * Use-vs-edit (docs/UX.md, Principle 3) keeps a record's *definition* behind ⋯ → Bewerken with
   * one save at the foot, and that rule was costing a task's page the same round trip for every
   * property on it: moving a deadline, handing the task to a colleague or bumping its priority
   * meant the pencil, a form over the whole page, and a save button at the bottom of it. So a
   * property gets its own surface here: the read view is the affordance (its value, plus a pencil
   * on hover), a click swaps in the editor for **that one field**, Opslaan posts the page's own
   * action carrying only what the editor renders, and Annuleren or Esc puts the value back.
   *
   * Three things the caller decides. `canEdit` is the same API permission the page's edit mode
   * mirrors — without it the read view is just the value, never a prompt that would refuse
   * (#253). `action` is an update action that patches only the fields the posted form carries,
   * so nothing else on the record is touched by a save from here. And the `editor` snippet
   * renders the control(s) *inside* the form and is handed `submit`, so a select or a checkbox
   * may save on change (`saveOnChange` then hides the Opslaan button that would repeat the pick)
   * — the instant one-click control the status select in use mode has always been.
   *
   * The read view may carry links (a client, a project), which is why the wrapper is not a
   * `<button>`: a click on an `<a>` navigates and nothing else, a click on the value opens, and
   * the pencil is a real button so the keyboard has one.
   */
  import { Pencil } from "@lucide/svelte";
  import type { Snippet } from "svelte";

  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";

  import Button from "./Button.svelte";

  let {
    label,
    action = "?/update",
    canEdit = false,
    saveOnChange = false,
    labelledEditor = false,
    id,
    class: className = "",
    read,
    editor,
    after,
    onopen,
    onclose,
    beforeSubmit,
  }: {
    /** The field's caption, above both the value and the editor. */
    label: string;
    /** The page's update action; it must patch only what the posted form carries. */
    action?: string;
    /** The same API permission the page's edit mode mirrors. */
    canEdit?: boolean;
    /** The editor saves itself on change — a select, a checkbox — so no Opslaan is drawn. */
    saveOnChange?: boolean;
    /** The editor draws its own caption (a multi-control editor), so ours is not repeated. */
    labelledEditor?: boolean;
    /** Base id: the form is `${id}-form`, and an editor's control should take `${id}` itself. */
    id: string;
    class?: string;
    /** The value as a reader sees it. */
    read: Snippet;
    /** The control(s), rendered inside the form. */
    editor: Snippet<[{ formId: string; submit: () => void; cancel: () => void }]>;
    /** Anything the editing state needs *beside* the form — a second form cannot nest in it. */
    after?: Snippet<[{ close: () => void }]>;
    /** The editor opened — a host that fetches options lazily hears it here. */
    onopen?: () => void;
    /** The editor closed, by a save, Annuleren or Esc — a host resets live picks here. */
    onclose?: () => void;
    /**
     * Asked with the posted fields before the save leaves — a `false` keeps the editor open and
     * posts nothing. For the host that has a question to ask first (a task in a series: does
     * the new assignee apply to this one or to every following?), which then raises its own
     * dialog and re-submits the form by id once it has an answer.
     */
    beforeSubmit?: (formData: FormData) => boolean;
  } = $props();

  const busy = new InFlight();
  let editing = $state(false);
  // Remounts the editor on every open, so a cancelled draft never survives into the next one.
  let session = $state(0);
  // A refusal is shown beside the field it refused, not at the foot of the page.
  let error = $state<string | null>(null);
  let formEl = $state<HTMLFormElement | undefined>();
  const formId = $derived(`${id}-form`);

  function open() {
    if (!canEdit || editing) return;
    session += 1;
    error = null;
    editing = true;
    onopen?.();
  }
  function close() {
    if (!editing) return;
    editing = false;
    onclose?.();
  }
  function submit() {
    formEl?.requestSubmit();
  }
  function onkeydown(event: KeyboardEvent) {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
    }
  }
  /** A click on a link or a control inside the read view is that thing's, not ours. */
  function onReadClick(event: MouseEvent) {
    const target = event.target as HTMLElement | null;
    if (target?.closest("a, button, input, select, textarea")) return;
    open();
  }
</script>

<div class="min-w-0 {className}">
  {#if editing}
    {#if !labelledEditor}
      <label for={id} class="mb-1 block text-xs font-medium text-text-muted">{label}</label>
    {/if}
    <!-- Esc cancels from anywhere inside the editor, so the listener sits on the form. -->
    <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
    <form
      id={formId}
      bind:this={formEl}
      method="POST"
      {action}
      class="space-y-2"
      {onkeydown}
      use:enhance={busy.wrap("save", ({ formData, cancel }) => {
        if (beforeSubmit && !beforeSubmit(formData)) {
          cancel();
          return;
        }
        return async ({ update, result }) => {
          if (result.type === "success") {
            // Edits what exists: never reset (docs/UX.md, "Saving must never blank the form").
            await update({ reset: false });
            close();
            return;
          }
          if (result.type === "failure") {
            const data = result.data as { error?: string } | undefined;
            error = data?.error ?? "errors.validation";
            return;
          }
          await update({ reset: false });
        };
      })}
    >
      {#key session}
        {@render editor({ formId, submit, cancel: close })}
      {/key}
      {#if error}
        <p class="text-xs text-red-600 dark:text-red-400">{t(error)}</p>
      {/if}
      <div class="flex items-center justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-3 py-1.5 text-sm text-text hover:bg-surface"
          onclick={close}>{t("common.cancel")}</button
        >
        {#if !saveOnChange}
          <Button size="sm" loading={busy.active}>{t("common.save")}</Button>
        {/if}
      </div>
    </form>
    {#if after}
      {@render after({ close })}
    {/if}
  {:else if canEdit}
    <span class="mb-1 block text-xs font-medium text-text-muted">{label}</span>
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <div
      class="group -m-1 flex cursor-pointer items-start gap-1 rounded-lg p-1 hover:bg-surface"
      onclick={onReadClick}
    >
      <div class="min-w-0 flex-1">
        {@render read()}
      </div>
      <button
        type="button"
        class="shrink-0 rounded p-0.5 text-text-muted opacity-0 transition-opacity hover:text-brand focus-visible:opacity-100 group-hover:opacity-100"
        aria-label={t("common.edit_field", { label })}
        onclick={open}
      >
        <Pencil size={13} aria-hidden="true" />
      </button>
    </div>
  {:else}
    <span class="mb-1 block text-xs font-medium text-text-muted">{label}</span>
    {@render read()}
  {/if}
</div>
