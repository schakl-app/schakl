<script lang="ts">
  /**
   * The contactmomenten timeline body, shared by the company panel (API provider dict) and the
   * project/contact/task entity panels (typed loads) — only the plumbing differs per host.
   *
   * Rules it renders (issue #22, agreed with the owner):
   * - A gmail row arrives **pending**: the team sees metadata (participants, subject, snippet);
   *   the body only exists after the mailbox owner approves. Rejection removes the row.
   * - Approve / reject / remap belong to the mailbox owner alone — no admin override — so the
   *   affordances only render for them. The API enforces the same, harder.
   * - Manual rows (meeting / call / note) get the ordinary ⋯ Bewerken / Verwijderen, gated by
   *   the caller's own/any write scope.
   *
   * **Host contract:** the page must expose `?/createInteraction`, `?/updateInteraction`,
   * `?/deleteInteraction`, `?/approveInteraction` and `?/rejectInteraction` form actions —
   * spread `interactionActions` from `./actions.server` into its `actions`.
   */
  import {
    ArrowDownLeft,
    ArrowRightLeft,
    ArrowUpRight,
    CheckCircle2,
    Pencil,
    Plus,
    Trash2,
    X,
  } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import { InFlight } from "$lib/core/submit.svelte";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";

  import CloseTaskDialog from "./CloseTaskDialog.svelte";
  import { type InteractionItem, kindIcon } from "./format";
  import InteractionDetailModal from "./InteractionDetailModal.svelte";
  import InteractionForm from "./InteractionForm.svelte";
  import InteractionMoveDialog from "./InteractionMoveDialog.svelte";

  let {
    items,
    total,
    prefill = {},
    members = [],
  }: {
    items: InteractionItem[];
    total: number;
    /** The host entity's link, stamped on rows added from this panel. */
    prefill?: Record<string, string | null | undefined>;
    /** Org members, for the note editor's @mention autocomplete (#151). */
    members?: { user_id: string; full_name: string | null; email: string }[];
  } = $props();

  const mentionCandidates = $derived(
    members.map((m) => ({ id: m.user_id, name: m.full_name || m.email })),
  );

  const me = $derived(page.data.user?.id ?? null);
  const canWrite = $derived(can(page.data.user, "interactions.interaction.write"));

  let showCreate = $state(false);
  let showEdit = $state(false);
  let editing = $state<InteractionItem | null>(null);
  let showMove = $state(false);
  let moving = $state<InteractionItem | null>(null);
  let showCloseTask = $state(false);
  let closingWith = $state<InteractionItem | null>(null);

  let deleteId = $state("");
  let confirmDelete = $state(false);
  let showReject = $state(false);
  let rejecting = $state<InteractionItem | null>(null);

  const busy = new InFlight();

  // Clicking a row opens the shared detail modal (#184) rather than expanding inline — a long
  // email no longer stretches the panel or scrolls it sideways, and its line breaks survive.
  let showDetail = $state(false);
  let detailItem = $state<InteractionItem | null>(null);
  function openDetail(item: InteractionItem) {
    detailItem = item;
    showDetail = true;
  }

  // A busy record's timeline grows without bound: show the newest few, reveal the rest in
  // place — the activity feed's pattern (docs/UX.md). Server truncation (PANEL_LIMIT) is
  // reported separately below.
  const COLLAPSED = 3;
  let showAll = $state(false);
  const collapsible = $derived(items.length > COLLAPSED);
  const shown = $derived(collapsible && !showAll ? items.slice(0, COLLAPSED) : items);

  const isOwner = (item: InteractionItem) =>
    item.owner_user_id !== null && item.owner_user_id === me;
  const mayEdit = (item: InteractionItem) =>
    item.source === "manual" &&
    (isOwner(item)
      ? can(page.data.user, "interactions.interaction.write", "own")
      : can(page.data.user, "interactions.interaction.write", "any"));

  // Moving a manual row rides the ordinary write scope; a gmail row stays the mailbox
  // owner's call (the review rule) — the API enforces both, harder (#147).
  const mayMove = (item: InteractionItem) =>
    item.source === "gmail" ? isOwner(item) : mayEdit(item);

  /** Where this row also belongs (#147): clickable chips for links beyond the current host. */
  function linkChips(item: InteractionItem): { href: string; label: string }[] {
    const host = new Set(Object.keys(prefill));
    const chips: { href: string; label: string }[] = [];
    if (item.project_id && item.project_name && !host.has("project_id"))
      chips.push({ href: `/projects/${item.project_id}`, label: item.project_name });
    if (item.task_id && item.task_title && !host.has("task_id"))
      chips.push({ href: `/tasks/${item.task_id}`, label: item.task_title });
    if (item.contact_id && item.contact_name && !host.has("contact_id"))
      chips.push({ href: `/contacts/${item.contact_id}`, label: item.contact_name });
    return chips;
  }

  function menuItems(item: InteractionItem) {
    const entries = [];
    if (mayEdit(item)) {
      entries.push({
        label: t("common.edit"),
        icon: Pencil,
        onclick: () => {
          editing = item;
          showEdit = true;
        },
      });
    }
    if (mayMove(item)) {
      // A pending email is *assigned* (and optionally approved) rather than moved (#183).
      const pending = item.source === "gmail" && item.status === "pending";
      entries.push({
        label: pending ? t("interactions.assign") : t("interactions.move"),
        icon: ArrowRightLeft,
        onclick: () => {
          moving = item;
          showMove = true;
        },
      });
    }
    // Close the linked task with this moment (#157): team-visible rows only — a pending
    // email's content isn't approved yet, so it cannot justify a close.
    if (item.task_id && item.status === "logged" && canWrite) {
      entries.push({
        label: t("interactions.close_task"),
        icon: CheckCircle2,
        onclick: () => {
          closingWith = item;
          showCloseTask = true;
        },
      });
    }
    if (mayEdit(item)) {
      entries.push({
        label: t("common.delete"),
        icon: Trash2,
        danger: true,
        onclick: () => {
          deleteId = item.id;
          confirmDelete = true;
        },
      });
    }
    if (item.source === "gmail" && isOwner(item)) {
      entries.push({
        label: t("interactions.reject"),
        icon: X,
        danger: true,
        onclick: () => {
          rejecting = item;
          showReject = true;
        },
      });
    }
    return entries;
  }
</script>

<div class="mb-3 flex flex-wrap items-center justify-between gap-2">
  <p class="text-sm text-text-muted">{t("interactions.panel.count", { count: total })}</p>
  {#if canWrite}
    <button
      type="button"
      class="inline-flex items-center gap-1 text-sm font-medium text-brand hover:underline"
      onclick={() => (showCreate = true)}
    >
      <Plus size={16} aria-hidden="true" />
      {t("interactions.add")}
    </button>
  {/if}
</div>

{#if items.length === 0}
  <p class="py-4 text-sm text-text-muted">{t("interactions.panel.empty")}</p>
{:else}
  <ul class="divide-y divide-border">
    {#each shown as item (item.id)}
      {@const Icon = kindIcon(item.kind)}
      {@const chips = linkChips(item)}
      <!-- id anchor: a task's "afgerond met contactmoment" trail entry deep-links here (#157). -->
      <li id="interaction-{item.id}" class="scroll-mt-20 py-2.5">
        <div class="flex items-start gap-3">
          <Icon size={16} class="mt-0.5 shrink-0 text-text-muted" aria-hidden="true" />
          <!-- The row opens a detail modal (#184); the preview is a short, wrapped teaser. -->
          <button
            type="button"
            class="-mx-1.5 -my-1 min-w-0 flex-1 rounded-lg px-1.5 py-1 text-left hover:bg-surface"
            onclick={() => openDetail(item)}
          >
            <span class="flex flex-wrap items-center gap-x-2 gap-y-0.5">
              <span class="text-sm font-medium text-text">
                {item.subject || t(`interactions.kind.${item.kind}`)}
              </span>
              {#if item.kind === "email" && item.direction !== "none"}
                {#if item.direction === "inbound"}
                  <ArrowDownLeft size={13} class="text-text-muted" aria-hidden="true" />
                  <span class="sr-only">{t("interactions.direction.inbound")}</span>
                {:else}
                  <ArrowUpRight size={13} class="text-text-muted" aria-hidden="true" />
                  <span class="sr-only">{t("interactions.direction.outbound")}</span>
                {/if}
              {/if}
              {#if item.status === "pending"}
                <span
                  class="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-800 dark:bg-amber-500/15 dark:text-amber-400"
                >
                  {t("interactions.pending")}
                </span>
              {/if}
              {#if item.closes_task}
                <!-- This moment closed its linked task (#157). -->
                <span
                  class="rounded-full bg-green-100 px-2 py-0.5 text-[11px] font-medium text-green-800 dark:bg-green-500/15 dark:text-green-400"
                >
                  {t("interactions.closed_task")}
                </span>
              {/if}
            </span>
            <span class="mt-0.5 block text-xs text-text-muted">
              {fmtDateTime(item.occurred_at)}{#if item.owner_name}&nbsp;· {item.owner_name}{/if}
            </span>
            {#if item.snippet}
              <!-- First couple of lines only, wrapped — never a sideways scroll (#184). -->
              <span class="mt-0.5 line-clamp-2 break-words text-xs text-text-muted"
                >{item.snippet}</span
              >
            {/if}
          </button>

          {#if item.status === "pending" && isOwner(item)}
            <!-- The owner's call, made where the email shows up. Non-destructive → inline. -->
            <form method="POST" action="?/approveInteraction" use:enhance={busy.wrap(item.id)}>
              <input type="hidden" name="id" value={item.id} />
              <Button
                type="submit"
                variant="secondary"
                size="xs"
                loading={busy.is(item.id)}
                disabled={busy.active}
              >
                {t("interactions.approve")}
              </Button>
            </form>
          {/if}
          {#if menuItems(item).length > 0}
            <ActionsMenu compact items={menuItems(item)} />
          {/if}
        </div>

        {#if chips.length > 0}
          <!-- Where else this row lives (#147) — links, outside the expand button. Full text
               colour at `text-xs`: who it was with never reads quieter than the muted
               timestamp above it (#238). -->
          <div class="mt-1 flex flex-wrap gap-1 pl-7">
            {#each chips as chip (chip.href)}
              <a
                href={chip.href}
                class="rounded-full bg-surface px-2 py-0.5 text-xs text-text ring-1 ring-inset ring-border hover:text-brand"
              >
                {chip.label}
              </a>
            {/each}
          </div>
        {/if}
      </li>
    {/each}
  </ul>
  {#if collapsible}
    <button
      type="button"
      class="mt-3 text-xs font-medium text-brand hover:underline"
      onclick={() => (showAll = !showAll)}
    >
      {showAll ? t("common.show_less") : t("common.show_all", { count: items.length })}
    </button>
  {/if}
{/if}

{#if total > items.length}
  <p class="mt-3 border-t border-border pt-3 text-xs text-text-muted">
    {t("interactions.panel.truncated", { shown: items.length, total })}
  </p>
{/if}

<Modal bind:open={showCreate} title={t("interactions.add")}>
  <InteractionForm {prefill} mentions={mentionCandidates} onsaved={() => (showCreate = false)} />
</Modal>

<Modal bind:open={showEdit} title={t("interactions.edit")}>
  {#if editing}
    {#key editing.id}
      <InteractionForm
        interaction={editing}
        mentions={mentionCandidates}
        onsaved={() => (showEdit = false)}
      />
    {/key}
  {/if}
</Modal>

<Modal bind:open={showCloseTask} title={t("interactions.close_task_title")}>
  {#if closingWith}
    {#key closingWith.id}
      <CloseTaskDialog interaction={closingWith} onsaved={() => (showCloseTask = false)} />
    {/key}
  {/if}
</Modal>

<Modal
  bind:open={showMove}
  title={moving?.source === "gmail" && moving?.status === "pending"
    ? t("interactions.assign_title")
    : t("interactions.move_title")}
>
  {#if moving}
    {#key moving.id}
      <InteractionMoveDialog
        interaction={moving}
        approveAction="?/approveInteraction"
        onsaved={() => (showMove = false)}
      />
    {/key}
  {/if}
</Modal>

<!-- The full contact moment (#184): shared with the Interacties list page — the email reads
     properly (line breaks kept, long tokens wrapped, no sideways scroll) and a pending gmail
     row the viewer owns is reviewed (assign + approve / reject) in the same place. -->
<InteractionDetailModal bind:open={showDetail} item={detailItem} />

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("interactions.delete_title")}
  message={t("interactions.delete_message")}
  action="?/deleteInteraction"
  fields={{ id: deleteId }}
/>

<!-- Reject wants one more decision than a yes/no (also ignore the whole thread?), so it gets a
     small form of its own rather than a ConfirmDialog. -->
<Modal bind:open={showReject} title={t("interactions.reject_title")}>
  {#if rejecting}
    <form
      method="POST"
      action="?/rejectInteraction"
      class="space-y-4"
      use:enhance={busy.wrap("reject", () => async ({ update }) => {
        showReject = false;
        await update();
      })}
    >
      <input type="hidden" name="id" value={rejecting.id} />
      <p class="text-sm text-text-muted">{t("interactions.reject_message")}</p>
      <label class="flex items-center gap-2 text-sm text-text">
        <input type="checkbox" name="suppress_thread" value="1" />
        {t("interactions.reject_thread")}
      </label>
      <div class="flex justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm font-medium text-text hover:bg-surface"
          onclick={() => (showReject = false)}
        >
          {t("common.cancel")}
        </button>
        <Button type="submit" variant="danger" loading={busy.is("reject")} disabled={busy.active}>
          {t("interactions.reject")}
        </Button>
      </div>
    </form>
  {/if}
</Modal>
