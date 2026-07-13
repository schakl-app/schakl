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
    ExternalLink,
    Paperclip,
    Pencil,
    Plus,
    Trash2,
    X,
  } from "@lucide/svelte";

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

  import type { CustomFieldDefinition } from "$lib/core/customfields/types";
  import ContactQuickCreate from "$lib/modules/contacts/ContactQuickCreate.svelte";

  import CloseTaskDialog from "./CloseTaskDialog.svelte";
  import { type InteractionItem, kindIcon } from "./format";
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
  const canReadActivity = $derived(can(page.data.user, "activity.read"));

  // The interaction's own recorded trail (#152): written since #22, rendered nowhere until
  // now — an entity without a detail page shows its history inside the detail modal (#184).
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
  let showMove = $state(false);
  let moving = $state<InteractionItem | null>(null);
  let showCloseTask = $state(false);
  let closingWith = $state<InteractionItem | null>(null);

  // Creating a contact from an unknown email participant (#160). Contact custom-field
  // definitions load once, on first use — not every host page holds them.
  let showParticipantCreate = $state(false);
  let participantDraft = $state<{
    name: string;
    email: string;
    company: { id: string; name: string } | null;
  } | null>(null);
  let contactDefinitions = $state<CustomFieldDefinition[] | null>(null);

  async function createFromParticipant(
    item: InteractionItem,
    participant: { email: string; name?: string | null },
  ) {
    participantDraft = {
      name: participant.name ?? "",
      email: participant.email,
      company:
        item.company_id && item.company_name
          ? { id: item.company_id, name: item.company_name }
          : null,
    };
    if (contactDefinitions === null) {
      const response = await fetch("/api/v1/custom-fields/definitions?entity_type=contact", {
        headers: { accept: "application/json" },
      });
      contactDefinitions = response.ok ? await response.json() : [];
    }
    showParticipantCreate = true;
  }
  // An approved email's stored attachments (#180), fetched lazily when a row expands —
  // the list render must not cost a files call per row.
  interface AttachmentFile {
    id: string;
    filename: string;
    size_bytes: number;
  }
  let attachmentsFor = $state<Record<string, AttachmentFile[]>>({});
  async function loadAttachments(id: string) {
    if (attachmentsFor[id]) return;
    const response = await fetch(`/api/v1/files?entity_type=interaction&entity_id=${id}`, {
      headers: { accept: "application/json" },
    });
    attachmentsFor = { ...attachmentsFor, [id]: response.ok ? await response.json() : [] };
  }

  let deleteId = $state("");
  let confirmDelete = $state(false);
  let showReject = $state(false);
  let rejecting = $state<InteractionItem | null>(null);

  // Clicking a row opens a detail modal (#184) rather than expanding inline — a long email
  // no longer stretches the panel or scrolls it sideways, and its line breaks survive.
  let showDetail = $state(false);
  let detailItem = $state<InteractionItem | null>(null);
  function openDetail(item: InteractionItem) {
    detailItem = item;
    showDetail = true;
    trailFor = null;
    if (item.source === "gmail" && item.status === "logged") void loadAttachments(item.id);
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
  // A pending gmail row I own is reviewed (assign + approve/reject) inside the detail modal.
  const detailPending = $derived(
    detailItem != null &&
      detailItem.source === "gmail" &&
      detailItem.status === "pending" &&
      isOwner(detailItem),
  );
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
      <li class="py-2.5">
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

        {#if chips.length > 0}
          <!-- Where else this row lives (#147) — links, outside the expand button. -->
          <div class="mt-1 flex flex-wrap gap-1 pl-7">
            {#each chips as chip (chip.href)}
              <a
                href={chip.href}
                class="rounded-full bg-surface px-2 py-0.5 text-[11px] text-text-muted ring-1 ring-inset ring-border hover:text-brand"
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

{#if participantDraft}
  <ContactQuickCreate
    bind:open={showParticipantCreate}
    name={participantDraft.name}
    email={participantDraft.email}
    linkCompany={participantDraft.company}
    definitions={contactDefinitions ?? []}
    locale={(page.data.locale as string | undefined) ?? "nl"}
    action="?/createParticipantContact"
    error={(page.form?.qcError as string | undefined) ?? null}
  />
{/if}

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

<!-- The full contact moment (#184): the email reads properly here — line breaks kept, long
     tokens wrapped, no sideways scroll — and a pending email is reviewed (assign + approve /
     reject) in the same place. -->
<Modal bind:open={showDetail} title={detailItem?.subject || t("interactions.detail_title")}>
  {#if detailItem}
    {#key detailItem.id}
      {@const di = detailItem}
      <div class="space-y-3 text-sm">
        <p class="text-xs text-text-muted">
          {fmtDateTime(di.occurred_at)}{#if di.owner_name}&nbsp;· {di.owner_name}{/if}
        </p>

        {#if di.participants?.length}
          <div class="flex flex-wrap gap-1">
            {#each di.participants as p ((p.role ?? "to") + p.email)}
              {@const cc = p.role === "cc"}
              {#if p.contact_id}
                <a
                  href="/contacts/{p.contact_id}"
                  title={p.email}
                  class="rounded-full px-2 py-0.5 text-[11px] ring-1 ring-inset {cc
                    ? 'bg-surface text-text-muted ring-border'
                    : 'bg-brand/10 text-brand ring-brand/30'} hover:underline"
                >
                  {p.name || p.email}<span class="sr-only">
                    ({t(`interactions.role.${p.role ?? "to"}`)})</span
                  >
                </a>
              {:else if p.user_id}
                <span
                  title={p.email}
                  class="rounded-full px-2 py-0.5 text-[11px] ring-1 ring-inset ring-border {cc
                    ? 'bg-surface text-text-muted'
                    : 'bg-surface text-text'}"
                >
                  {p.name || p.email}<span class="sr-only">
                    ({t("interactions.participant_colleague")},
                    {t(`interactions.role.${p.role ?? "to"}`)})</span
                  >
                </span>
              {:else}
                <button
                  type="button"
                  title={p.email}
                  onclick={() => createFromParticipant(di, p)}
                  class="inline-flex items-center gap-0.5 rounded-full border border-dashed border-border px-2 py-0.5 text-[11px] {cc
                    ? 'text-text-muted'
                    : 'text-text'} hover:border-brand hover:text-brand"
                >
                  {p.name || p.email}
                  <Plus size={10} aria-hidden="true" />
                  <span class="sr-only">
                    {t("interactions.create_contact")}
                    ({t(`interactions.role.${p.role ?? "to"}`)})</span
                  >
                </button>
              {/if}
            {/each}
          </div>
        {/if}

        {#if di.body_text}
          {#if di.source === "gmail"}
            <!-- break-words so a lone long URL can't scroll the modal sideways (#184). -->
            <p class="whitespace-pre-wrap break-words text-sm text-text">{di.body_text}</p>
          {:else}
            <Markdown value={di.body_text} />
          {/if}
        {:else if di.snippet}
          <p class="text-sm text-text-muted">{di.snippet}</p>
          {#if di.source === "gmail" && di.status === "logged"}
            <p class="text-xs text-text-muted">{t("interactions.body_loading")}</p>
          {/if}
        {/if}

        {#if attachmentsFor[di.id]?.length}
          <div class="flex flex-wrap gap-1">
            {#each attachmentsFor[di.id] as file (file.id)}
              <a
                href={`/api/v1/files/${file.id}`}
                target="_blank"
                rel="noopener noreferrer"
                class="inline-flex items-center gap-1 rounded-full bg-surface px-2 py-0.5 text-[11px] text-text ring-1 ring-inset ring-border hover:text-brand"
              >
                <Paperclip size={11} aria-hidden="true" />
                {file.filename}
              </a>
            {/each}
          </div>
        {/if}

        {#if di.deep_link}
          <a
            href={di.deep_link}
            target="_blank"
            rel="noopener noreferrer"
            class="inline-flex items-center gap-1 text-xs font-medium text-brand hover:underline"
          >
            <ExternalLink size={12} aria-hidden="true" />
            {t("interactions.open_in_gmail")}
          </a>
        {/if}

        {#if detailPending}
          <!-- Review in place (#184): assign a client/project/task and approve, or reject. -->
          <div class="border-t border-border pt-3">
            <InteractionMoveDialog
              interaction={di}
              approveAction="?/approveInteraction"
              onsaved={() => (showDetail = false)}
            />
            <button
              type="button"
              class="mt-2 text-xs font-medium text-red-600 hover:underline dark:text-red-400"
              onclick={() => {
                rejecting = di;
                showDetail = false;
                showReject = true;
              }}
            >
              {t("interactions.reject")}
            </button>
          </div>
        {/if}

        {#if canReadActivity}
          <div class="border-t border-border pt-3">
            <button
              type="button"
              class="text-xs font-medium text-text-muted hover:text-brand"
              onclick={() => toggleTrail(di.id)}
            >
              {trailFor === di.id ? t("interactions.history_hide") : t("interactions.history")}
            </button>
            {#if trailFor === di.id}
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
