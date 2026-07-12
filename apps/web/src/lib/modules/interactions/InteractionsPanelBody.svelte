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
  import { ArrowDownLeft, ArrowUpRight, ExternalLink, Pencil, Plus, Trash2, X } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import ActivityFeed from "$lib/core/activity/ActivityFeed.svelte";
  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import Markdown from "$lib/core/ui/Markdown.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";

  import { type InteractionItem, KIND_ICONS } from "./format";
  import InteractionForm from "./InteractionForm.svelte";

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
  const canReadActivity = $derived(can(page.data.user, "activity.read"));

  // The interaction's own recorded trail (#152): written since #22, rendered nowhere until
  // now — an entity without a detail page shows its history inside the expanded row.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let trail = $state<any[] | null>(null);
  let trailFor = $state<string | null>(null);

  async function toggleTrail(id: string) {
    if (trailFor === id) {
      trailFor = null;
      return;
    }
    trailFor = id;
    trail = null;
    const response = await fetch(
      `/api/v1/activity?entity_type=interaction&entity_id=${id}&limit=50`,
      { headers: { accept: "application/json" } },
    );
    if (trailFor !== id) return; // the user toggled away while we were fetching
    trail = response.ok ? await response.json() : [];
  }

  let showCreate = $state(false);
  let showEdit = $state(false);
  let editing = $state<InteractionItem | null>(null);
  let deleteId = $state("");
  let confirmDelete = $state(false);
  let showReject = $state(false);
  let rejecting = $state<InteractionItem | null>(null);
  let expanded = $state<string | null>(null);

  const isOwner = (item: InteractionItem) => item.owner_user_id !== null && item.owner_user_id === me;
  const mayEdit = (item: InteractionItem) =>
    item.source === "manual" &&
    (isOwner(item)
      ? can(page.data.user, "interactions.interaction.write", "own")
      : can(page.data.user, "interactions.interaction.write", "any"));

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
    {#each items as item (item.id)}
      {@const Icon = KIND_ICONS[item.kind] ?? KIND_ICONS.note}
      {@const open = expanded === item.id}
      <li class="py-2.5">
        <div class="flex items-start gap-3">
          <Icon size={16} class="mt-0.5 shrink-0 text-text-muted" aria-hidden="true" />
          <button
            type="button"
            class="min-w-0 flex-1 text-left"
            onclick={() => (expanded = open ? null : item.id)}
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
            </span>
            <span class="mt-0.5 block text-xs text-text-muted">
              {fmtDateTime(item.occurred_at)}{#if item.owner_name}&nbsp;· {item.owner_name}{/if}
            </span>
            {#if !open && item.snippet}
              <span class="mt-0.5 block truncate text-xs text-text-muted">{item.snippet}</span>
            {/if}
          </button>

          {#if item.status === "pending" && isOwner(item)}
            <!-- The owner's call, made where the email shows up. Non-destructive → inline. -->
            <form method="POST" action="?/approveInteraction" use:enhance>
              <input type="hidden" name="id" value={item.id} />
              <button
                type="submit"
                class="rounded-lg border border-border px-2.5 py-1 text-xs font-medium text-text hover:bg-surface"
              >
                {t("interactions.approve")}
              </button>
            </form>
          {/if}
          {#if menuItems(item).length > 0}
            <ActionsMenu compact items={menuItems(item)} />
          {/if}
        </div>

        {#if open}
          <div class="mt-2 space-y-2 pl-7 text-sm">
            {#if item.participants?.length}
              <p class="text-xs text-text-muted">
                {item.participants.map((p) => p.name || p.email).join(", ")}
              </p>
            {/if}
            {#if item.body_text}
              {#if item.source === "gmail"}
                <p class="whitespace-pre-wrap text-sm text-text">{item.body_text}</p>
              {:else}
                <Markdown value={item.body_text} />
              {/if}
            {:else if item.snippet}
              <p class="text-sm text-text-muted">{item.snippet}</p>
              {#if item.source === "gmail" && item.status === "logged"}
                <!-- Approved seconds ago: the body fetch is on its way (docs/GOOGLE.md §6). -->
                <p class="text-xs text-text-muted">{t("interactions.body_loading")}</p>
              {/if}
            {/if}
            {#if item.deep_link}
              <a
                href={item.deep_link}
                target="_blank"
                rel="noopener noreferrer"
                class="inline-flex items-center gap-1 text-xs font-medium text-brand hover:underline"
              >
                <ExternalLink size={12} aria-hidden="true" />
                {t("interactions.open_in_gmail")}
              </a>
            {/if}
            {#if canReadActivity}
              <div>
                <button
                  type="button"
                  class="text-xs font-medium text-text-muted hover:text-brand"
                  onclick={() => toggleTrail(item.id)}
                >
                  {trailFor === item.id
                    ? t("interactions.history_hide")
                    : t("interactions.history")}
                </button>
                {#if trailFor === item.id}
                  <div class="mt-2 border-l-2 border-border pl-3">
                    {#if trail === null}
                      <p class="text-xs text-text-muted">{t("common.loading")}</p>
                    {:else}
                      <ActivityFeed items={trail} limit={50} />
                    {/if}
                  </div>
                {/if}
              </div>
            {/if}
          </div>
        {/if}
      </li>
    {/each}
  </ul>
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
      use:enhance={() =>
        async ({ update }) => {
          showReject = false;
          await update();
        }}
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
        <button
          type="submit"
          class="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >
          {t("interactions.reject")}
        </button>
      </div>
    </form>
  {/if}
</Modal>
