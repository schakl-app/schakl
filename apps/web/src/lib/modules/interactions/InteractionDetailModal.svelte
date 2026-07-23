<script lang="ts">
  /**
   * The full contact-moment detail (issue #184), shared by the company/project/contact/task
   * panels (`InteractionsPanelBody`) and the standalone Interacties list page. Clicking a row on
   * either surface opens this instead of expanding inline or navigating away, so a long email
   * reads properly — line breaks kept, long tokens wrapped, **no sideways scroll** — and a
   * pending gmail row the viewer owns is reviewed (assign + approve / reject) in the same place.
   *
   * Gmail-style conversation view (#272): when the opened row folds a conversation
   * (`conversation_count > 1`), the whole thread is fetched lazily and rendered newest-first —
   * the newest message expanded, older ones collapsed to a one-line summary each, expandable in
   * place. Review / history stay scoped to the original row (`item`); a pending row never folds,
   * so those affordances never land on a historical message.
   *
   * Self-contained: it owns its lazy attachment fetch, its activity-trail toggle, the
   * unknown-participant → contact quick-create, and its own reject form. The host only needs to
   * expose the standard interaction form actions (`?/approveInteraction`, `?/rejectInteraction`,
   * `?/createParticipantContact`) — both hosts spread `interactionActions`, so they already do.
   */
  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { ChevronUp, Ellipsis, ExternalLink, Paperclip, Plus } from "@lucide/svelte";

  import { SvelteSet } from "svelte/reactivity";

  import ActivityFeed from "$lib/core/activity/ActivityFeed.svelte";
  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Markdown from "$lib/core/ui/Markdown.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";

  import type { CustomFieldDefinition } from "$lib/core/customfields/types";
  import ContactQuickCreate from "$lib/modules/contacts/ContactQuickCreate.svelte";

  import { type InteractionItem, isMailRow } from "./format";
  import { cleanSnippet, snippetPreview } from "./snippet";
  import InteractionMoveDialog from "./InteractionMoveDialog.svelte";
  import { splitQuotedTrail } from "./quoted";

  let {
    open = $bindable(false),
    item,
    approveAction = "?/approveInteraction",
    rejectAction = "?/rejectInteraction",
    participantAction = "?/createParticipantContact",
  }: {
    open?: boolean;
    item: InteractionItem | null;
    approveAction?: string;
    rejectAction?: string;
    participantAction?: string;
  } = $props();

  const me = $derived(page.data.user?.id ?? null);
  const canReadActivity = $derived(can(page.data.user, "activity.read"));
  const isOwner = (i: InteractionItem) => i.owner_user_id !== null && i.owner_user_id === me;
  // A pending gmail row I own is reviewed (assign + approve/reject) right here (#184). A pending
  // row never folds (#272), so review always concerns a single, unfolded message.
  const detailPending = $derived(
    item != null && item.source === "gmail" && item.status === "pending" && isOwner(item),
  );

  // --- the conversation (#272): [item] for an ordinary single email, the full thread for a fold //
  let messages = $state<InteractionItem[]>([]);
  // Which messages are open in full; the rest show a one-line summary. Newest defaults open.
  const expanded = new SvelteSet<string>();
  const threaded = $derived(messages.length > 1);

  async function loadThread(anchor: InteractionItem) {
    const response = await fetch(`/api/v1/interactions/${anchor.id}/thread`, {
      headers: { accept: "application/json" },
    });
    if (!open || item?.id !== anchor.id) return; // the modal moved on or closed while fetching
    const thread: InteractionItem[] = response.ok ? await response.json() : [anchor];
    messages = thread.length ? thread : [anchor];
    // The thread is newest-first and the anchor is its newest member: keep that one open.
    expanded.clear();
    expanded.add(messages[0]?.id ?? anchor.id);
    if (isMailRow(messages[0]) && messages[0].status === "logged") {
      void loadAttachments(messages[0].id);
    }
  }

  function toggleMessage(id: string) {
    if (expanded.has(id)) {
      expanded.delete(id);
    } else {
      expanded.add(id);
      const msg = messages.find((m) => m.id === id);
      if (msg && isMailRow(msg) && msg.status === "logged") void loadAttachments(id);
    }
  }

  // --- lazy attachments (#180): never a files call per list row, only on open --------------- //
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

  // The activity trail (#152), rendered nowhere until now: an interaction has no detail page,
  // so its history lives inside this modal (#184). Scoped to the original row (#272).
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
    if (trailFor !== id) return;
    trail = response.ok ? await response.json() : [];
  }

  // Opening a row (#180/#152/#272): seed the conversation with the row itself, load the newest
  // message's extras, and — only when it actually folds one — fetch the rest of the thread.
  $effect(() => {
    if (!open || !item) return;
    const anchor = item;
    trailFor = null;
    quotedExpanded.clear();
    messages = [anchor];
    expanded.clear();
    expanded.add(anchor.id);
    if (isMailRow(anchor) && anchor.status === "logged") void loadAttachments(anchor.id);
    if ((anchor.conversation_count ?? 1) > 1) void loadThread(anchor);
  });

  // A long email conversation shows only the current message; the quoted history folds behind
  // the ⋯ toggle, Gmail's own trimmed-content gesture — per message, collapsed again per open.
  const quotedExpanded = new SvelteSet<string>();
  function toggleQuoted(id: string) {
    if (quotedExpanded.has(id)) quotedExpanded.delete(id);
    else quotedExpanded.add(id);
  }
  function bodyPartsFor(msg: InteractionItem) {
    return isMailRow(msg) && msg.body_text ? splitQuotedTrail(msg.body_text) : null;
  }

  // --- unknown participant → contact quick-create (#160) ------------------------------------ //
  let showParticipantCreate = $state(false);
  let participantDraft = $state<{
    name: string;
    email: string;
    company: { id: string; name: string } | null;
  } | null>(null);
  let contactDefinitions = $state<CustomFieldDefinition[] | null>(null);
  async function createFromParticipant(
    i: InteractionItem,
    participant: { email: string; name?: string | null },
  ) {
    participantDraft = {
      name: participant.name ?? "",
      email: participant.email,
      company: i.company_id && i.company_name ? { id: i.company_id, name: i.company_name } : null,
    };
    if (contactDefinitions === null) {
      const response = await fetch("/api/v1/custom-fields/definitions?entity_type=contact", {
        headers: { accept: "application/json" },
      });
      contactDefinitions = response.ok ? await response.json() : [];
    }
    showParticipantCreate = true;
  }

  // --- reject (#22), self-contained so both hosts get it without wiring their own ----------- //
  let showReject = $state(false);
  const busy = new InFlight();
</script>

{#snippet messageBody(di: InteractionItem)}
  {@const bodyParts = bodyPartsFor(di)}
  <div class="space-y-3 text-sm">
    <div class="flex items-start justify-between gap-2">
      <p class="text-xs text-text-muted">
        {fmtDateTime(di.occurred_at)}{#if di.owner_name}&nbsp;· {di.owner_name}{/if}
      </p>
      {#if threaded}
        <!-- Collapse this message back to its one-line summary. -->
        <button
          type="button"
          onclick={() => toggleMessage(di.id)}
          title={t("interactions.thread_collapse")}
          class="shrink-0 text-text-muted hover:text-brand"
        >
          <ChevronUp size={16} aria-hidden="true" />
          <span class="sr-only">{t("interactions.thread_collapse")}</span>
        </button>
      {/if}
    </div>

    {#if di.closes_task}
      <!-- This moment closed its linked task (#157) — say so, and link to the task. -->
      <div class="flex items-center gap-2">
        <span
          class="inline-flex items-center gap-1 rounded-full bg-green-100 px-2 py-0.5 text-[11px] font-medium text-green-800 dark:bg-green-500/15 dark:text-green-400"
        >
          {t("interactions.closed_task")}
        </span>
        {#if di.task_id && di.task_title}
          <a href="/tasks/{di.task_id}" class="text-xs text-brand hover:underline"
            >{di.task_title}</a
          >
        {/if}
      </div>
    {/if}

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
      {#if isMailRow(di)}
        <!-- break-words so a lone long URL can't scroll the modal sideways (#184). -->
        <p class="whitespace-pre-wrap break-words text-sm text-text">
          {bodyParts?.head ?? di.body_text}
        </p>
        {#if bodyParts?.trail}
          <button
            type="button"
            onclick={() => toggleQuoted(di.id)}
            aria-expanded={quotedExpanded.has(di.id)}
            title={quotedExpanded.has(di.id)
              ? t("interactions.quoted_hide")
              : t("interactions.quoted_show")}
            class="inline-flex items-center rounded-full border border-border bg-surface px-2 py-0.5 text-text-muted hover:border-brand hover:text-brand"
          >
            <Ellipsis size={14} aria-hidden="true" />
            <span class="sr-only">
              {quotedExpanded.has(di.id)
                ? t("interactions.quoted_hide")
                : t("interactions.quoted_show")}
            </span>
          </button>
          {#if quotedExpanded.has(di.id)}
            <p
              class="whitespace-pre-wrap break-words border-l-2 border-border pl-3 text-sm text-text-muted"
            >
              {bodyParts.trail}
            </p>
          {/if}
        {/if}
      {:else}
        <Markdown value={di.body_text} />
      {/if}
    {:else if di.snippet}
      <!-- The whole snippet here — this is the detail — but decoded: it arrives from Gmail
           HTML-escaped, and `&#39;` in the middle of a sentence is not a sentence (#263). -->
      <p class="whitespace-pre-wrap break-words text-sm text-text-muted">
        {cleanSnippet(di.snippet)}
      </p>
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
  </div>
{/snippet}

{#snippet messageSummary(di: InteractionItem)}
  <!-- A collapsed thread member: one line, click to expand in place. -->
  <button
    type="button"
    onclick={() => toggleMessage(di.id)}
    class="-mx-1.5 block w-full rounded-lg px-1.5 py-1.5 text-left hover:bg-surface"
  >
    <span class="flex items-center justify-between gap-2">
      <span class="truncate text-xs font-medium text-text">
        {di.owner_name ?? t("interactions.kind.email")}
      </span>
      <span class="shrink-0 text-[11px] text-text-muted">{fmtDateTime(di.occurred_at)}</span>
    </span>
    <span class="mt-0.5 block truncate text-xs text-text-muted">
      {snippetPreview(di.body_text || di.snippet, 100) || di.subject || ""}
    </span>
    <span class="sr-only">{t("interactions.thread_show_message")}</span>
  </button>
{/snippet}

<Modal bind:open title={item?.subject || t("interactions.detail_title")}>
  {#if item}
    {#key item.id}
      <div class="space-y-3 text-sm">
        {#if threaded}
          <p class="text-xs font-medium text-text-muted">
            {t("interactions.conversation_count", { count: messages.length })}
          </p>
        {/if}

        <div class="divide-y divide-border">
          {#each messages as msg (msg.id)}
            <div class="py-3 first:pt-0 last:pb-0">
              {#if expanded.has(msg.id)}
                {@render messageBody(msg)}
              {:else}
                {@render messageSummary(msg)}
              {/if}
            </div>
          {/each}
        </div>

        {#if detailPending}
          <!-- Review in place (#184): assign a client/project/task and approve, or reject.
               Scoped to the original row (#272) — a pending row never folds. -->
          <div class="border-t border-border pt-3">
            <InteractionMoveDialog
              interaction={item}
              {approveAction}
              onsaved={() => (open = false)}
            />
            <button
              type="button"
              class="mt-2 text-xs font-medium text-red-600 hover:underline dark:text-red-400"
              onclick={() => (showReject = true)}
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
              onclick={() => toggleTrail(item.id)}
            >
              {trailFor === item.id ? t("interactions.history_hide") : t("interactions.history")}
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
    action={participantAction}
    error={(page.form?.qcError as string | undefined) ?? null}
  />
{/if}

<!-- Reject wants one more decision than yes/no (ignore the whole thread too), so its own form. -->
<Modal bind:open={showReject} title={t("interactions.reject_title")}>
  {#if item}
    <form
      method="POST"
      action={rejectAction}
      class="space-y-4"
      use:enhance={busy.wrap("", () => async ({ update }) => {
        showReject = false;
        open = false;
        await update();
      })}
    >
      <input type="hidden" name="id" value={item.id} />
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
        <Button type="submit" variant="danger" loading={busy.active}>
          {t("interactions.reject")}
        </Button>
      </div>
    </form>
  {/if}
</Modal>
