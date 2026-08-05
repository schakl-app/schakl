<script lang="ts" module>
  import type { Component } from "svelte";

  /** One module-specific bulk action, contributed by the list (the interacties review trio). */
  export interface BulkAction {
    label: string;
    icon?: Component;
    onclick: () => void;
    danger?: boolean;
    /**
     * How many of the selected rows this action can actually do. Rendered beside the label
     * whenever it is fewer than the selection, and disables the button at zero — a control that
     * silently did less than it said is the failure this prevents (docs/UX.md, #299).
     */
    eligible?: number;
  }
</script>

<script lang="ts">
  /**
   * Acting on several rows at once: a **mode**, entered with the ✎ in the toolbar.
   *
   * A list is for reading. Ticking boxes is not, which is why the boxes are not there until you
   * ask for them: pressing ✎ is what turns the list into something you are editing, and it is
   * the same gesture that puts Bewerken and Verwijderen on screen. Pressing it again puts the
   * list back and drops whatever was picked.
   *
   * That ordering is the whole design, and the previous attempt had it backwards — a permanent
   * checkbox gutter on every list plus a dropdown labelled "Bulkacties", so every reader paid
   * for a writer's feature and the actions were a click further away than the boxes that fed
   * them. Here nothing is visible until it is wanted, and then all of it is: the buttons sit
   * beside the ✎ rather than behind a second click, because by the time you have ticked three
   * rows you already know which of them you want.
   *
   * Gating mirrors the API exactly (§15, and the API is the boundary): Bewerken needs the
   * entity's write permission, Verwijderen its delete permission, and a control nobody may use
   * is not drawn. When *nothing* survives, neither is the ✎ — there is no mode to enter.
   */
  import { page } from "$app/state";
  import { Pencil, Trash2 } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";

  import BulkEditDialog from "./BulkEditDialog.svelte";
  import type { BulkFieldDef } from "./types";

  let {
    selecting = $bindable(false),
    selected = $bindable([]),
    fields = [],
    writePermission,
    deletePermission,
    deleteMessage,
    items = [],
    updateAction = "?/bulkUpdate",
    deleteAction = "?/bulkDelete",
    fieldErrors = null,
  }: {
    /** Selection mode. The list passes this straight to `DataTable`'s `selectable`. */
    selecting?: boolean;
    /** The picked ids, bound from the `DataTable`. Cleared here when the mode ends. */
    selected?: string[];
    /** The fields this entity can be bulk-edited on; empty means no Bewerken. */
    fields?: BulkFieldDef[];
    /** The entity's own write key. Omit together with `fields` for a delete-only list. */
    writePermission?: string;
    /** The entity's own delete key. Omit for a list with no bulk delete. */
    deletePermission?: string;
    /** The confirmation copy — entity-specific, because "12 clients" is not "12 rows". */
    deleteMessage?: string;
    /** Module-specific actions, shown before the generic pair. */
    items?: BulkAction[];
    updateAction?: string;
    deleteAction?: string;
    fieldErrors?: Record<string, string> | null;
  } = $props();

  let showEdit = $state(false);
  let confirmDelete = $state(false);

  const canEdit = $derived(
    fields.length > 0 && (!writePermission || can(page.data.user, writePermission)),
  );
  const canDelete = $derived(!!deletePermission && can(page.data.user, deletePermission));
  /** Nothing this user may do here means no ✎ at all, not a mode that leads nowhere. */
  const usable = $derived(canEdit || canDelete || items.length > 0);
  const count = $derived(selected.length);

  function toggle() {
    selecting = !selecting;
    // Leaving the mode drops the picks: the boxes are gone, so a selection nobody can see must
    // not survive to be acted on by the next thing that opens.
    if (!selecting) selected = [];
  }

  /** A count beside a label, only when the action is doing less than the selection suggests. */
  const partial = (eligible: number | undefined) =>
    eligible !== undefined && eligible < count ? ` (${eligible})` : "";

  const button =
    "inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-border px-3 py-1.5" +
    " text-sm text-text-muted disabled:cursor-not-allowed disabled:opacity-40";
</script>

{#if usable}
  <!-- The mode switch. Icon only: it is the same ✎ that means "edit" everywhere else, and a
       toolbar that spells out "Bulkacties" spends its width explaining a button nobody has
       pressed yet. `aria-pressed` is what carries the state to a screen reader. -->
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

  {#if selecting}
    <span class="text-xs text-text-muted">{t("table.selected", { count })}</span>

    <!-- Disabled until something is ticked, with the reason in the title: in this mode the
         buttons are the point, so hiding them until a row is picked would leave the user in a
         mode whose purpose is invisible. -->
    {#each items as item (item.label)}
      {@const Icon = item.icon}
      <button
        type="button"
        class="{button} {item.danger
          ? 'hover:border-red-400 hover:text-red-600 dark:hover:border-red-500 dark:hover:text-red-400'
          : 'hover:border-brand hover:text-brand'}"
        disabled={count === 0 || item.eligible === 0}
        title={count === 0 ? t("bulk.select_first") : undefined}
        onclick={item.onclick}
      >
        {#if Icon}<Icon size={14} />{/if}
        {item.label}{partial(item.eligible)}
      </button>
    {/each}

    {#if canEdit}
      <button
        type="button"
        class="{button} hover:border-brand hover:text-brand"
        disabled={count === 0}
        title={count === 0 ? t("bulk.select_first") : undefined}
        onclick={() => (showEdit = true)}
      >
        <Pencil size={14} />
        {t("bulk.edit")}
      </button>
    {/if}

    {#if canDelete}
      <button
        type="button"
        class="{button} hover:border-red-400 hover:text-red-600 dark:hover:border-red-500 dark:hover:text-red-400"
        disabled={count === 0}
        title={count === 0 ? t("bulk.select_first") : undefined}
        onclick={() => (confirmDelete = true)}
      >
        <Trash2 size={14} />
        {t("common.delete")}
      </button>
    {/if}
  {/if}

  {#if canEdit}
    <BulkEditDialog bind:open={showEdit} {fields} {selected} action={updateAction} {fieldErrors} />
  {/if}
  {#if canDelete}
    <!-- Every delete confirms (docs/UX.md), and this one says how many records it is about. -->
    <ConfirmDialog
      bind:open={confirmDelete}
      title={t("bulk.delete_title")}
      message={deleteMessage ?? t("bulk.delete_message", { count })}
      action={deleteAction}
      fields={{ ids: selected.join(",") }}
    />
  {/if}
{/if}
