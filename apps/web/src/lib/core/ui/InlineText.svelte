<script lang="ts">
  /**
   * One free-text field, edited in place (#455).
   *
   * Use-vs-edit (docs/UX.md, Principle 3) puts a record's *definition* behind ⋯ → Bewerken with
   * one save at the foot — right for the title, the relations, the budget, and wrong for the one
   * field people change ten times a day: a task's description read "Voeg een omschrijving toe…"
   * in use mode and the prompt did nothing, and a client's notes sat inside a slide-over behind
   * every other field of the record. So a single field gets its own surface: the read view *is*
   * the affordance (the text, or its placeholder, plus a pencil), a click swaps in the editor
   * for that one field, Opslaan posts the page's own `?/update` carrying only this field, and
   * Annuleren or Esc puts the text back. The one-save rule is about *edit mode*; this is not
   * edit mode, it is one field.
   *
   * Two things the caller decides. `canEdit` is the same API permission the page's edit mode
   * mirrors — without it the read view is just the text, and an empty field is a dash rather
   * than a prompt that would refuse (#253). And `action` is the page's update action, which
   * already patches only the fields the posted form carries, so nothing else on the record is
   * touched by a save from here.
   */
  import { Pencil } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";

  import Button from "./Button.svelte";
  import Markdown from "./Markdown.svelte";
  import RichTextEditor from "./RichTextEditor.svelte";

  let {
    value = "",
    name,
    action = "?/update",
    placeholder = "",
    canEdit = false,
    rows = 4,
    scope,
    id = `inline-${name}`,
  }: {
    /** The stored text (markdown source). */
    value?: string;
    /** The field the page's update action reads. */
    name: string;
    action?: string;
    /** What an editor sees where there is no text yet; a reader sees a dash. */
    placeholder?: string;
    /** The same API permission the page's edit mode mirrors. */
    canEdit?: boolean;
    rows?: number;
    /** Passed through to the editor's #task / @mention candidates. */
    scope?: { companyId?: string | null; projectId?: string | null };
    id?: string;
  } = $props();

  const busy = new InFlight();
  let editing = $state(false);
  // Remounts the editor on every open, so a cancelled draft never survives into the next one.
  let session = $state(0);

  function open() {
    if (!canEdit) return;
    session += 1;
    editing = true;
  }
  function close() {
    editing = false;
  }
  function onkeydown(event: KeyboardEvent) {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
    }
  }
</script>

{#if editing}
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <form
    method="POST"
    {action}
    class="space-y-2"
    {onkeydown}
    use:enhance={busy.wrap("save", () => async ({ update, result }) => {
      // Edits what exists: never reset (docs/UX.md, "Saving must never blank the form").
      await update({ reset: false });
      if (result.type === "success") close();
    })}
  >
    {#key session}
      <RichTextEditor {id} {name} {rows} {value} {placeholder} {scope} />
    {/key}
    <div class="flex items-center justify-end gap-2">
      <button
        type="button"
        class="rounded-lg border border-border px-3 py-1.5 text-sm text-text hover:bg-surface"
        onclick={close}>{t("common.cancel")}</button
      >
      <Button size="sm" loading={busy.active}>{t("common.save")}</Button>
    </div>
  </form>
{:else if canEdit}
  <!-- The read view is the affordance: the whole block opens the editor, and the pencil says so
       for whoever does not try clicking prose. `role="button"` on a div rather than a <button>,
       because rendered markdown is block content a <button> may not contain. -->
  <div
    role="button"
    tabindex="0"
    class="group -m-2 cursor-text rounded-lg p-2 hover:bg-surface"
    onclick={open}
    onkeydown={(event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        open();
      }
    }}
  >
    {#if value}
      <Markdown {value} />
    {:else}
      <p class="text-sm text-text-muted">{placeholder}</p>
    {/if}
    <span
      class="mt-1 inline-flex items-center gap-1 text-xs text-text-muted opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100"
    >
      <Pencil size={12} aria-hidden="true" />
      {t("common.edit")}
    </span>
  </div>
{:else if value}
  <Markdown {value} />
{:else}
  <p class="text-sm text-text-muted">—</p>
{/if}
