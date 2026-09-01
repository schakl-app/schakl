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
   * as tall as it was — and while it *is* on it **sticks to the top** (#331). Everything here
   * describes a decision being made further down a 200-row page: the count, the scope, and each
   * action's `eligible` suffix. Scrolled out of sight, the user ticks rows and only finds out at
   * the top of the page whether they ticked eleven or twelve.
   */
  import { page } from "$app/state";
  import { Pencil, Trash2, X } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";

  import BulkEditDialog from "./BulkEditDialog.svelte";
  import type { BulkConfig } from "./types";

  let {
    selecting = false,
    selected = $bindable([]),
    fields = [],
    writePermission,
    deletePermission,
    deleteMessage,
    deleteEligible,
    deleteDisabledReason,
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

  /**
   * A count beside a label, whenever the action is doing something other than what the selection
   * suggests: fewer, because some rows are ineligible — or **more**, because a ticked row stands
   * for several (the interactions queue folds a thread to one row, and approving it approves
   * the thread). Either way the number a button prints has to be the number it will act on.
   */
  const partial = (eligible: number | undefined) =>
    eligible !== undefined && eligible !== count ? ` (${eligible})` : "";

  const button =
    "inline-flex shrink-0 cursor-pointer items-center gap-1.5 whitespace-nowrap rounded-lg" +
    " border border-border bg-surface-raised px-3 py-1.5 text-sm text-text-muted" +
    " disabled:cursor-not-allowed disabled:opacity-40";
</script>

{#if selecting}
  <!--
    What sticks is the **wrapper**, not the tinted bar. The bar's own background is `bg-brand/5`
    and translucent, so rows would scroll visibly through it; the wrapper is the page's own opaque
    ground, and its padding is what stops a sliver of row showing above the rounded frame. The
    negative top margin gives that padding back to the flow, so an unstuck bar sits exactly where
    it used to. `z-20` is over the table and under `ActionsMenu`'s panel (`z-30`), which measures
    itself against the viewport for precisely this kind of container.
  -->
  <div class="sticky top-0 z-20 -mt-2 bg-surface pb-3 pt-2" data-testid="bulk-bar">
    <!--
      One line below `sm`: stuck to the top with six actions, a wrapping strip eats a third of a
      phone. So the actions scroll sideways instead of stacking, and only the strip scrolls — the
      count and the way out never leave the screen.
    -->
    <div
      class="flex items-center gap-2 rounded-xl border border-brand/30 bg-brand/5 px-3 py-2 sm:flex-wrap"
    >
      <span class="shrink-0 text-xs font-medium text-text" data-testid="bulk-count"
        >{t("table.selected", { count })}</span
      >
      {#if count > 0}
        <!-- The discoverable way out. The ✎ also drops the selection, but it reads as "leave this
             mode", and someone who ticked eleven rows by mistake wants a control that says only
             "forget what I picked" — reachable now that the strip does not scroll away. -->
        <button
          type="button"
          class="inline-flex shrink-0 cursor-pointer items-center rounded p-0.5 text-text-muted hover:text-text"
          data-testid="bulk-clear"
          onclick={() => (selected = [])}
          title={t("table.selection_clear")}
          aria-label={t("table.selection_clear")}
        >
          <X size={13} />
        </button>
      {/if}
      <span class="hidden shrink-0 text-xs text-text-muted sm:inline"
        >· {t("table.selection_page_only")}</span
      >

      <div class="ml-auto flex min-w-0 items-center gap-2 overflow-x-auto sm:flex-wrap">
        <!-- Disabled until something is ticked, with the reason in the title: in this mode the
           buttons are the point, so hiding them until a row is picked would leave the user in a
           mode whose purpose is invisible.

           "Nothing is selected" outranks the action's own `disabledReason`, and the order is
           load-bearing: an action whose reason is "none of these qualify" is *also* ineligible
           over an empty selection, so preferring the reason would answer the wrong question
           first — you would be told why the rows you have not picked yet do not qualify. -->
        {#each items as item (item.label)}
          {@const Icon = item.icon}
          {@const blocked = count === 0 || item.eligible === 0 || !!item.disabledReason}
          {@const reason = count === 0 ? t("bulk.select_first") : item.disabledReason}
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
              title={reason}
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
          <!-- Same three rules the `items` loop above obeys, for the same reason: an action that
               can do nothing with what is ticked must say so instead of offering itself and
               answering "0 verwijderd" afterwards. "Nothing is selected" still outranks the
               entity's own reason — over an empty selection that reason answers the wrong
               question. -->
          <button
            type="button"
            class="{button} hover:border-red-400 hover:text-red-600 dark:hover:border-red-500 dark:hover:text-red-400"
            disabled={count === 0 || deleteEligible === 0 || !!deleteDisabledReason}
            title={count === 0 ? t("bulk.select_first") : deleteDisabledReason}
            onclick={() => (confirmDelete = true)}
          >
            <Trash2 size={14} />
            {t("common.delete")}{partial(deleteEligible)}
          </button>
        {/if}
      </div>
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
