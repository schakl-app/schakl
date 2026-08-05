<script lang="ts">
  /**
   * The ✎ that turns a list into something you are editing — and nothing else.
   *
   * It is deliberately **the last control in the toolbar**, on every list: it is the only one
   * there that changes what the *rows* do rather than what the list shows, so it sits apart from
   * Export/Import/Kolommen instead of among them. Pressing it opens the selection: checkboxes on
   * the rows, and `BulkBar`'s own strip above the table. Pressing it again closes both and drops
   * the picks — a selection nobody can see must not survive to be acted on by the next thing.
   *
   * Split from the bar (rather than growing the toolbar in place) because the two belong in
   * different places: the switch belongs with the list's controls, the actions belong with the
   * rows they act on. Sharing a props shape (`BulkConfig`) means a page still configures it
   * once and spreads it into both.
   */
  import { page } from "$app/state";
  import { Pencil } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";

  import type { BulkConfig } from "./types";

  let {
    selecting = $bindable(false),
    selected = $bindable([]),
    fields = [],
    writePermission,
    deletePermission,
    items = [],
    // Accepted and ignored: a page spreads one config object into this and `BulkBar`.
    deleteMessage: _deleteMessage = undefined,
    updateAction: _updateAction = undefined,
    deleteAction: _deleteAction = undefined,
    fieldErrors: _fieldErrors = undefined,
  }: BulkConfig & { selecting?: boolean; selected?: string[] } = $props();

  /** Nothing this user may do here means no ✎ at all, not a mode that leads nowhere. */
  const usable = $derived(
    (fields.length > 0 && (!writePermission || can(page.data.user, writePermission))) ||
      (!!deletePermission && can(page.data.user, deletePermission)) ||
      items.length > 0,
  );

  function toggle() {
    selecting = !selecting;
    if (!selecting) selected = [];
  }
</script>

{#if usable}
  <!-- Icon only: it is the same ✎ that means "edit" everywhere else, and a toolbar that spells
       out "Bulkacties" spends its width explaining a button nobody has pressed yet.
       `aria-pressed` is what carries the state to a screen reader. -->
  <button
    type="button"
    class="inline-flex cursor-pointer items-center rounded-lg border p-2
      {selecting
      ? 'border-brand bg-brand/10 text-brand'
      : 'border-border text-text-muted hover:border-brand hover:text-brand'}"
    aria-pressed={selecting}
    aria-label={selecting ? t("bulk.select_done") : t("bulk.select")}
    title={selecting ? t("bulk.select_done") : t("bulk.select")}
    onclick={toggle}
  >
    <Pencil size={15} />
  </button>
{/if}
