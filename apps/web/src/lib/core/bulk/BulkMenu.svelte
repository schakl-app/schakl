<script lang="ts" module>
  import type { Component } from "svelte";

  /** One module-specific bulk action, contributed by the list (the interacties review trio). */
  export interface BulkMenuItem {
    label: string;
    icon?: Component;
    onclick: () => void;
    danger?: boolean;
    /**
     * How many of the selected rows this action can actually do. Rendered beside the label
     * whenever it is fewer than the selection, and disables the item at zero — a button that
     * silently did less than it said is the failure this prevents (docs/UX.md, #299).
     */
    eligible?: number;
  }
</script>

<script lang="ts">
  /**
   * Bulk actions for a list's current selection, behind one ✎ in the toolbar's top-right.
   *
   * They used to be a bar that appeared above the table the moment anything was ticked, which
   * had two problems worth naming. It **moved the table down** as you selected — on a list you
   * tick from the top, the rows walk away from the cursor mid-gesture. And it put four
   * write controls, one of them destructive, directly under the pointer, which is the exact
   * shape docs/UX.md keeps record actions out of rows for: an exposed Delete gets clicked by
   * accident. Behind a menu they sit still, they read as a set, and reaching them is a
   * deliberate second click — the ⋯ rule for a row, applied to a selection.
   *
   * It lives beside Export/Import and Kolommen because that cluster is already where a list's
   * own controls live, and unlike them it is about the rows you picked rather than the list —
   * hence the count on the trigger, and the disabled state that says what is missing when you
   * have picked nothing.
   *
   * Gating mirrors the API exactly (§15, and the API is the boundary): the edit item needs the
   * entity's write permission, the delete item its delete permission, and an item nobody may
   * use is not drawn at all. When *nothing* survives, neither is the trigger.
   */
  import { page } from "$app/state";
  import { Pencil, Trash2 } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";

  import BulkEditDialog from "./BulkEditDialog.svelte";
  import type { BulkFieldDef } from "./types";

  let {
    selected,
    fields = [],
    writePermission,
    deletePermission,
    deleteMessage,
    items = [],
    updateAction = "?/bulkUpdate",
    deleteAction = "?/bulkDelete",
    fieldErrors = null,
  }: {
    /** The list's current selection — bound from the `DataTable`. */
    selected: string[];
    /** The fields this entity can be bulk-edited on; empty means no edit item. */
    fields?: BulkFieldDef[];
    /** The entity's own write key. Omit together with `fields` for a delete-only list. */
    writePermission?: string;
    /** The entity's own delete key. Omit for a list with no bulk delete. */
    deletePermission?: string;
    /** The confirmation copy — entity-specific, because "12 clients" is not "12 rows". */
    deleteMessage?: string;
    /** Module-specific actions, listed above the generic pair. */
    items?: BulkMenuItem[];
    updateAction?: string;
    deleteAction?: string;
    fieldErrors?: Record<string, string> | null;
  } = $props();

  let open = $state(false);
  let root: HTMLElement | undefined = $state();
  let showEdit = $state(false);
  let confirmDelete = $state(false);

  const canEdit = $derived(
    fields.length > 0 && (!writePermission || can(page.data.user, writePermission)),
  );
  const canDelete = $derived(!!deletePermission && can(page.data.user, deletePermission));
  /** Nothing this user may do here means no control at all, not a menu that only refuses. */
  const usable = $derived(canEdit || canDelete || items.length > 0);
  const count = $derived(selected.length);

  function choose(run: () => void) {
    open = false;
    run();
  }

  /** A count beside a label, only when the action is doing less than the selection suggests. */
  const partial = (eligible: number | undefined) =>
    eligible !== undefined && eligible < count ? ` (${eligible})` : "";
</script>

<svelte:window
  onclick={(e) => {
    if (open && root && !root.contains(e.target as Node)) open = false;
  }}
  onkeydown={(e) => {
    if (e.key === "Escape") open = false;
  }}
/>

{#if usable}
  <div class="relative" bind:this={root}>
    <button
      type="button"
      class="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm
        disabled:cursor-not-allowed disabled:opacity-50
        {count > 0
        ? 'border-brand text-brand'
        : 'border-border text-text-muted hover:border-brand hover:text-brand'}"
      aria-expanded={open}
      aria-haspopup="menu"
      disabled={count === 0}
      title={count === 0 ? t("bulk.select_first") : undefined}
      onclick={() => (open = !open)}
    >
      <Pencil size={15} />
      {t("bulk.actions")}
      {#if count > 0}
        <span class="rounded-full bg-brand px-1.5 py-0.5 text-[10px] font-semibold text-white">
          {count}
        </span>
      {/if}
    </button>

    {#if open}
      <div
        role="menu"
        class="absolute right-0 z-30 mt-1 w-60 rounded-lg border border-border bg-surface-raised py-1 shadow-lg"
      >
        <p class="px-3 pb-1 pt-1.5 text-xs text-text-muted">
          {t("table.selected", { count })} · {t("table.selection_page_only")}
        </p>
        <div class="border-t border-border pt-1">
          {#each items as item (item.label)}
            {@const Icon = item.icon}
            <button
              type="button"
              role="menuitem"
              disabled={item.eligible === 0}
              onclick={() => choose(item.onclick)}
              class="flex w-full cursor-pointer items-center gap-2 px-4 py-2 text-left text-sm hover:bg-surface disabled:cursor-not-allowed disabled:opacity-50
                {item.danger
                ? 'text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950'
                : 'text-text'}"
            >
              {#if Icon}<Icon size={15} class={item.danger ? "" : "text-text-muted"} />{/if}
              {item.label}{partial(item.eligible)}
            </button>
          {/each}
          {#if canEdit}
            <button
              type="button"
              role="menuitem"
              onclick={() => choose(() => (showEdit = true))}
              class="flex w-full cursor-pointer items-center gap-2 px-4 py-2 text-left text-sm text-text hover:bg-surface"
            >
              <Pencil size={15} class="text-text-muted" />
              {t("bulk.edit")}
            </button>
          {/if}
          {#if canDelete}
            <button
              type="button"
              role="menuitem"
              onclick={() => choose(() => (confirmDelete = true))}
              class="flex w-full cursor-pointer items-center gap-2 px-4 py-2 text-left text-sm text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950"
            >
              <Trash2 size={15} />
              {t("common.delete")}
            </button>
          {/if}
        </div>
      </div>
    {/if}
  </div>

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
