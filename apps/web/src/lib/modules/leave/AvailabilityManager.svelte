<script lang="ts">
  /**
   * A freelancer's availability: the exceptions on top of the week they were engaged under.
   *
   * The compact surface — a list and a form under it — shared by the freelancer's own page
   * (`/leave`) and the manager's roster ⋯ menu, so the two cannot drift (docs/UX.md). The
   * cross-person table on `/leave/availability` is the same rows through the same form; both
   * read `availability.ts` so a move is folded into one line exactly once.
   *
   * Whose week it writes is the `userId` prop — omitted for "me", which is what the API resolves
   * an absent `user_id` to; anybody else's needs `leave.availability.write:any` and the API
   * re-checks it.
   *
   * The contract is deliberately not editable here: it is the agency's record of what was
   * agreed, and "I'm also free on Wednesdays from now on" is a weekly extra, not a rewrite of
   * the period somebody was engaged under.
   */
  import { ArrowRight, CalendarPlus, CalendarX, Pencil, Trash2 } from "@lucide/svelte";

  import { fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";

  import AvailabilityForm from "./AvailabilityForm.svelte";
  import {
    availabilityKindText,
    availabilityRepeatText,
    availabilityRowId,
    availabilityRows,
    availabilityWindowText,
    type AvailabilityEntry,
  } from "./availability";

  let {
    entries = [],
    userId = "",
    error = null,
    highlightId = "",
    ondone,
  }: {
    /** This person's exception rows, already narrowed to the read window. */
    entries?: AvailabilityEntry[];
    /** Whose week a save writes; `""` = the signed-in user (the API's own default). */
    userId?: string;
    error?: string | null;
    /** The row an agenda chip deep-linked to (#106's shape): scrolled to and marked on arrival. */
    highlightId?: string;
    /** A row landed: the host may close its modal and own the confirmation (#271). */
    ondone?: () => void;
  } = $props();

  const rows = $derived(availabilityRows(entries));
  const highlightRowId = $derived(availabilityRowId(rows, highlightId));

  let deleteId = $state("");
  let deleteOpen = $state(false);
  let editEntry = $state<AvailabilityEntry | null>(null);
  let editOpen = $state(false);

  // Scroll the deep-linked row into view once the list is on screen. A chip that navigates to a
  // page and leaves the reader to find the row themselves is half a link.
  $effect(() => {
    if (!highlightRowId) return;
    document
      .getElementById(`availability-${highlightRowId}`)
      ?.scrollIntoView({ block: "center", behavior: "smooth" });
  });
</script>

<div class="space-y-4">
  {#if rows.length > 0}
    <ul class="divide-y divide-border rounded-lg border border-border text-sm">
      {#each rows as row (row.primary.id)}
        {@const primary = row.primary}
        {@const lit = primary.id === highlightRowId}
        <li
          id="availability-{primary.id}"
          class="flex items-start gap-3 px-3 py-2 {lit
            ? 'bg-brand/5 ring-1 ring-inset ring-brand'
            : ''}"
        >
          <div class="min-w-0 flex-1">
            {#if row.kind === "move"}
              <span class="flex flex-wrap items-center gap-1.5 text-text">
                <span class="line-through decoration-text-muted">
                  {fmtNumericDate(row.from.date)}
                </span>
                <ArrowRight size={14} class="text-text-muted" />
                <span class="font-medium">{fmtNumericDate(row.to.date)}</span>
              </span>
            {:else}
              <span class="flex items-center gap-1.5 font-medium text-text">
                {#if primary.kind === "extra"}
                  <CalendarPlus size={14} />
                {:else}
                  <CalendarX size={14} />
                {/if}
                {fmtNumericDate(primary.date)}
              </span>
            {/if}
            <span class="mt-0.5 block text-xs text-text-muted">
              {availabilityKindText(row)}
              · {availabilityWindowText(primary)}
              {#if availabilityRepeatText(primary)}· {availabilityRepeatText(primary)}{/if}
              {#if primary.note}· {primary.note}{/if}
            </span>
          </div>
          <!-- Correcting a day is the ordinary act on a row somebody already wrote; before this
               the only control was 🗑, so extending a repeat meant retyping the whole thing —
               and on a move, both halves, because the delete takes the pair (#368). -->
          <button
            type="button"
            class="rounded-lg p-1 text-text-muted hover:text-brand"
            title={t("common.edit")}
            aria-label={t("common.edit")}
            onclick={() => {
              editEntry = primary;
              editOpen = true;
            }}
          >
            <Pencil size={14} />
          </button>
          <button
            type="button"
            class="rounded-lg p-1 text-text-muted hover:text-red-600 dark:hover:text-red-400"
            title={t("common.delete")}
            aria-label={t("common.delete")}
            onclick={() => {
              deleteId = primary.id;
              deleteOpen = true;
            }}
          >
            <Trash2 size={14} />
          </button>
        </li>
      {/each}
    </ul>
  {:else}
    <p class="rounded-lg bg-surface px-3 py-2 text-xs text-text-muted">
      {t("leave.availability.empty")}
    </p>
  {/if}

  <div class="border-t border-border pt-4">
    <AvailabilityForm {userId} {error} {ondone} />
  </div>
</div>

<!-- Editing one row of a move edits that row: the pair binds *deletion*, because half a swap is
     a statement nobody made, while correcting the replacement day's hours says nothing about the
     day that was dropped. -->
<Modal bind:open={editOpen} title={t("leave.availability.edit")}>
  {#if editEntry}
    {#key editEntry.id}
      <AvailabilityForm entry={editEntry} {error} ondone={() => (editOpen = false)} />
    {/key}
  {/if}
</Modal>

<!-- Deleting one half of a move removes both — the API owns that rule, and the confirmation
     says so rather than leaving somebody unavailable on a day they had agreed to swap. -->
<ConfirmDialog
  bind:open={deleteOpen}
  title={t("common.delete")}
  message={t("leave.availability.delete_confirm")}
  action="?/deleteAvailability"
  fields={{ id: deleteId }}
  confirmLabel={t("common.delete")}
/>
