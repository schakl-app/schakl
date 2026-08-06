<script lang="ts">
  /**
   * What you can do to the rows you picked — a strip of its own, directly above the table.
   *
   * It is a **separate section, not more toolbar**, and that is the point. The first attempt put
   * these buttons inline beside Export/Import/Kolommen, where two things went wrong: the row
   * grew and reflowed every time the mode opened, and the new controls read as more of the same
   * list chrome. They are not — Export changes what you *get*, Verwijderen changes what *is*.
   * So they land on their own line, in the brand-tinted frame this app already uses for a live
   * selection, between the list's controls and the rows themselves.
   *
   * It renders only while the mode is on (`BulkToggle`), so a list nobody is editing is exactly
   * as tall as it was.
   */
  import { page } from "$app/state";
  import { Pencil, Trash2 } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";

  import BulkEditDialog from "./BulkEditDialog.svelte";
  import type { BulkConfig } from "./types";

  let {
    selecting = false,
    selected = [],
    fields = [],
    writePermission,
    deletePermission,
    deleteMessage,
    items = [],
    updateAction = "?/bulkUpdate",
    deleteAction = "?/bulkDelete",
    fieldErrors = null,
  }: BulkConfig & { selecting?: boolean; selected?: string[] } = $props();

  let showEdit = $state(false);
  let confirmDelete = $state(false);

  const canEdit = $derived(
    fields.length > 0 && (!writePermission || can(page.data.user, writePermission)),
  );
  const canDelete = $derived(!!deletePermission && can(page.data.user, deletePermission));
  const count = $derived(selected.length);

  /** A count beside a label, only when the action is doing less than the selection suggests. */
  const partial = (eligible: number | undefined) =>
    eligible !== undefined && eligible < count ? ` (${eligible})` : "";

  const button =
    "inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-border" +
    " bg-surface-raised px-3 py-1.5 text-sm text-text-muted" +
    " disabled:cursor-not-allowed disabled:opacity-40";
</script>

{#if selecting}
  <div
    class="mb-3 flex flex-wrap items-center gap-2 rounded-xl border border-brand/30 bg-brand/5 px-3 py-2"
  >
    <span class="text-xs font-medium text-text">{t("table.selected", { count })}</span>
    <span class="text-xs text-text-muted">· {t("table.selection_page_only")}</span>

    <div class="ml-auto flex flex-wrap items-center gap-2">
      <!-- Disabled until something is ticked, with the reason in the title: in this mode the
           buttons are the point, so hiding them until a row is picked would leave the user in a
           mode whose purpose is invisible. -->
      {#each items as item (item.label)}
        {@const Icon = item.icon}
        {@const blocked = count === 0 || item.eligible === 0 || !!item.disabledReason}
        {@const tone = item.danger
          ? "hover:border-red-400 hover:text-red-600 dark:hover:border-red-500 dark:hover:text-red-400"
          : "hover:border-brand hover:text-brand"}
        {#if item.href && !blocked}
          <!-- A download is a navigation, so it is a link: middle-click and "save as" work, and
               there is no handler pretending to be one. `data-sveltekit-reload` for the reason
               `ImpexBar` gives — the target is a download endpoint, never a client-side route.
               Blocked falls through to the button below: a disabled anchor does not exist, and
               an <a> that refuses on click is #253's "link that always refuses". -->
          <a href={item.href} class="{button} {tone}" data-sveltekit-reload>
            {#if Icon}<Icon size={14} />{/if}
            {item.label}{partial(item.eligible)}
          </a>
        {:else}
          <button
            type="button"
            class="{button} {tone}"
            disabled={blocked}
            title={item.disabledReason ?? (count === 0 ? t("bulk.select_first") : undefined)}
            onclick={item.onclick}
          >
            {#if Icon}<Icon size={14} />{/if}
            {item.label}{partial(item.eligible)}
          </button>
        {/if}
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
    </div>
  </div>
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
