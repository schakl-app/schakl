<script lang="ts">
  /**
   * One meeting: the run while the worker has it, the review desk once it does not, the
   * record once a person confirmed it.
   *
   * The review desk is the dictation sheet's posture (docs/VOICE.md): the model drafted, a
   * colleague reads every line beside the words it was drawn from, corrects, and only then
   * does anything become a record. Two rules follow. **Every claim shows its evidence** — the
   * quote and the timestamp the model gave, and a mark where the quote was *not* found in the
   * transcript — because a summary reads plausibly whether or not it is true. And **the
   * transcript stays on the screen** while the draft is edited: a misheard name is only fixable
   * while the words are in view, and the speaker labels are named here, beside the lines they
   * label.
   *
   * The roster is the third thing on the desk. A speaker label is paired with a *person* — a
   * colleague, a contact of the client, or a name — and the action items are drawn **by side
   * and then by person** (what the agency took on, under each colleague; what the client took
   * on, under each contact; the rest), because that is how either side reads a list of action
   * items: for their own name. Naming the speakers after the draft was written is common, so
   * *Notulen opnieuw opstellen* writes the minutes again over the same transcript, at no audio
   * cost, with the people known this time.
   */
  import AlertTriangle from "@lucide/svelte/icons/alert-triangle";
  import ArrowLeft from "@lucide/svelte/icons/arrow-left";
  import Check from "@lucide/svelte/icons/check";
  import Download from "@lucide/svelte/icons/download";
  import Plus from "@lucide/svelte/icons/plus";
  import RefreshCw from "@lucide/svelte/icons/refresh-cw";
  import Sparkles from "@lucide/svelte/icons/sparkles";
  import X from "@lucide/svelte/icons/x";
  import { untrack } from "svelte";

  import { enhance } from "$app/forms";
  import { invalidate } from "$app/navigation";
  import { page } from "$app/state";
  import { aiEnabled } from "$lib/core/ai";
  import { fmtDateTime } from "$lib/core/format";
  import { t, tn } from "$lib/core/i18n";
  import { pollWhile } from "$lib/core/poll.svelte";
  import { returnHref } from "$lib/core/screen-position.svelte";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Card from "$lib/core/ui/Card.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import Markdown from "$lib/core/ui/Markdown.svelte";
  import { memberLabel } from "$lib/core/members";
  import { companyArchivedLabel, splitCompanyOptions } from "$lib/modules/companies/picker";
  import {
    fmtClock,
    fmtDuration,
    inFlight,
    kindLabel,
    sourceLabel,
  } from "$lib/modules/meetings/format";
  import MeetingAIRevise from "$lib/modules/meetings/MeetingAIRevise.svelte";
  import MeetingExportDialog from "$lib/modules/meetings/MeetingExportDialog.svelte";
  import MeetingStatusPill from "$lib/modules/meetings/MeetingStatusPill.svelte";
  import ParticipantsEditor, {
    type Participant,
  } from "$lib/modules/meetings/ParticipantsEditor.svelte";
  import type {
    MinutesActionItem,
    MinutesDecision,
    MinutesDraft,
    TranscriptSegment,
  } from "$lib/modules/meetings/types";
  import { projectArchivedLabel, splitProjectOptions } from "$lib/modules/projects/picker";

  let { data, form } = $props();

  const meeting = $derived(data.meeting);
  // The generated types mark every defaulted field optional; the API always sends them.
  const segments = $derived((meeting.segments ?? []) as TranscriptSegment[]);
  const speakers = $derived((meeting.speakers ?? {}) as Record<string, string>);
  const members = $derived(data.members);
  const taskIds = $derived(meeting.task_ids ?? []);
  const busy = new InFlight();
  let confirmOpen = $state(false);
  let confirmDelete = $state(false);
  let confirmAudio = $state(false);
  let exportOpen = $state(false);

  // The worker owns the row for a while: ask again until it does not (`pollWhile`'s rule).
  pollWhile(
    () => inFlight(meeting.status),
    () => invalidate("meetings:meeting"),
  );

  /**
   * The draft under review. Copied off the row once per meeting, then the reviewer's — a
   * re-read after a save must not overwrite a field they are typing in. `untrack` on the read
   * of our own state, or the effect that seeds the draft restarts on every keystroke into it.
   */
  type Draft = Required<
    Pick<MinutesDraft, "summary" | "topics" | "decisions" | "action_items" | "open_questions">
  > & { title: string | null; truncated: boolean; partial_input: boolean };
  function toDraft(minutes: MinutesDraft): Draft {
    const copy = structuredClone($state.snapshot(minutes)) as MinutesDraft;
    return {
      title: copy.title ?? null,
      summary: copy.summary ?? "",
      topics: copy.topics ?? [],
      decisions: copy.decisions ?? [],
      action_items: copy.action_items ?? [],
      open_questions: copy.open_questions ?? [],
      truncated: copy.truncated ?? false,
      partial_input: copy.partial_input ?? false,
    };
  }
  let draft = $state<Draft | null>(null);
  let draftFor = $state<string | null>(null);
  $effect(() => {
    const row = meeting;
    if (row.status !== "review" || !row.minutes) return;
    if (untrack(() => draftFor) === row.id) return;
    draft = toDraft(row.minutes);
    draftFor = row.id;
  });

  /**
   * The roster under edit — the same copy-once rule as the draft, and re-seeded after its own
   * save so a label the API just stored is what the editor shows. `participantsRev` is what the
   * save bumps; the row's `updated_at` would also move on every draft save.
   */
  let participants = $state<Participant[]>([]);
  let participantsFor = $state<string | null>(null);
  $effect(() => {
    const row = meeting;
    const key = `${row.id}:${JSON.stringify(row.participants ?? [])}`;
    if (untrack(() => participantsFor) === key) return;
    participants = structuredClone($state.snapshot(row.participants ?? [])).map((p) => ({
      name: p.name,
      user_id: p.user_id ?? null,
      contact_id: p.contact_id ?? null,
      speaker: p.speaker ?? null,
    }));
    participantsFor = key;
  });
  const participantsPayload = $derived(JSON.stringify(participants));
  const participantsDirty = $derived(
    JSON.stringify(
      (meeting.participants ?? []).map((p) => ({
        name: p.name,
        user_id: p.user_id ?? null,
        contact_id: p.contact_id ?? null,
        speaker: p.speaker ?? null,
      })),
    ) !== participantsPayload,
  );

  const speakerLabels = $derived(
    Array.from(new Set(segments.map((s) => s.speaker).filter((s): s is string => !!s))),
  );
  function speakerName(label: string | null | undefined): string {
    if (!label) return "";
    return speakers[label] ?? label;
  }
  // A transcript with words and no labels is a speech model that answers text only — said by
  // name, because it looks exactly like a recording in which nobody could be told apart.
  const undiarized = $derived(!meeting.diarized && !!(segments.length || meeting.transcript_text));

  /**
   * "Who took this on" as one control. `u:<id>` is a colleague (a participant or anybody on
   * staff), `c:<id>` a contact of the client (a participant), `n:<name>` a free-text owner —
   * and typing an unknown name makes one. Exactly one of the item's three owner fields is set
   * by a pick; the API refuses a colleague and a contact on the same item anyway.
   */
  const ownerItems = $derived.by(() => {
    const items: { value: string; label: string; hint?: string }[] = [];
    const seen: string[] = [];
    for (const p of participants) {
      const value = p.user_id
        ? `u:${p.user_id}`
        : p.contact_id
          ? `c:${p.contact_id}`
          : `n:${p.name}`;
      if (seen.includes(value)) continue;
      seen.push(value);
      items.push({
        value,
        label: p.name,
        hint: p.user_id
          ? t("party.employee")
          : p.contact_id
            ? t("party.contact")
            : t("meetings.participants.other"),
      });
    }
    for (const m of members) {
      const value = `u:${m.user_id}`;
      if (seen.includes(value) || m.is_active === false) continue;
      seen.push(value);
      items.push({ value, label: memberLabel(m), hint: t("party.employee") });
    }
    return items;
  });
  function ownerValue(item: MinutesActionItem): string {
    if (item.owner_contact_id) return `c:${item.owner_contact_id}`;
    if (item.assignee_user_id) return `u:${item.assignee_user_id}`;
    if (item.owner_label) return `n:${item.owner_label}`;
    return "";
  }
  function setOwner(item: MinutesActionItem, value: string) {
    item.assignee_user_id = value.startsWith("u:") ? value.slice(2) : null;
    item.owner_contact_id = value.startsWith("c:") ? value.slice(2) : null;
    item.owner_label = value.startsWith("n:") ? value.slice(2) : null;
    // A colleague's item is ours to do; anybody else's is minuted unless the reviewer ticks it.
    item.create_task = !!item.assignee_user_id;
  }
  function ownerName(item: MinutesActionItem): string {
    const value = ownerValue(item);
    if (!value) return "";
    const found = ownerItems.find((o) => o.value === value);
    return found?.label ?? item.owner_label ?? "";
  }

  type Side = "agency" | "client" | "other";
  interface OwnerGroup {
    key: string;
    name: string;
    indices: number[];
  }
  /** The action items by side, each side by owner — the shape the minutes print in too. */
  function groupItems(items: MinutesActionItem[]): { side: Side; groups: OwnerGroup[] }[] {
    const sides: Record<Side, Map<string, OwnerGroup>> = {
      agency: new Map(),
      client: new Map(),
      other: new Map(),
    };
    items.forEach((item, index) => {
      const side: Side = item.owner_contact_id
        ? "client"
        : item.assignee_user_id
          ? "agency"
          : "other";
      const key = ownerValue(item).toLowerCase();
      const group = sides[side].get(key) ?? { key, name: ownerName(item), indices: [] };
      group.indices.push(index);
      sides[side].set(key, group);
    });
    return (["agency", "client", "other"] as Side[])
      .filter((side) => sides[side].size)
      .map((side) => ({ side, groups: Array.from(sides[side].values()) }));
  }
  const draftGroups = $derived(draft ? groupItems(draft.action_items) : []);
  const doneGroups = $derived(
    meeting.status === "done" && meeting.minutes
      ? groupItems((meeting.minutes.action_items ?? []) as MinutesActionItem[])
      : [],
  );

  const companyPicker = $derived(
    splitCompanyOptions(data.companies, { selectedId: meeting.company_id ?? "" }),
  );
  const projectPicker = $derived(
    splitProjectOptions(data.projects, {
      selectedId: meeting.project_id ?? "",
      companyId: meeting.company_id ?? "",
    }),
  );

  /** The whole draft as one field: the shape is nested and a form cannot say so in flat inputs. */
  const payload = $derived(
    draft
      ? JSON.stringify({
          ...draft,
          title: draft.title?.trim() || null,
          topics: draft.topics.filter((x) => x.heading.trim() && x.text.trim()),
          decisions: draft.decisions.filter((x) => x.text.trim()),
          action_items: draft.action_items.filter((x) => x.title.trim()),
          open_questions: draft.open_questions.filter((x) => x.trim()),
        })
      : "",
  );
  const tasksToCreate = $derived(
    draft?.action_items.filter((x) => x.create_task && x.title.trim()).length ?? 0,
  );
  const unverified = $derived(
    (draft?.decisions.filter((d) => !d.verified).length ?? 0) +
      (draft?.action_items.filter((a) => !a.verified).length ?? 0),
  );

  function addDecision() {
    if (!draft) return;
    draft.decisions = [...draft.decisions, { text: "", at: null, quote: null, verified: true }];
  }
  function addItem() {
    if (!draft) return;
    draft.action_items = [
      ...draft.action_items,
      {
        title: "",
        description: null,
        assignee_user_id: null,
        owner_contact_id: null,
        owner_label: null,
        due_date: null,
        at: null,
        quote: null,
        verified: true,
        create_task: true,
      },
    ];
  }
  function removeDecision(index: number) {
    if (!draft) return;
    draft.decisions = draft.decisions.filter((_, i) => i !== index);
  }
  function removeItem(index: number) {
    if (!draft) return;
    draft.action_items = draft.action_items.filter((_, i) => i !== index);
  }
  function addQuestion() {
    if (!draft) return;
    draft.open_questions = [...draft.open_questions, ""];
  }
  function removeQuestion(index: number) {
    if (!draft) return;
    draft.open_questions = draft.open_questions.filter((_, i) => i !== index);
  }
  function addTopic() {
    if (!draft) return;
    draft.topics = [...draft.topics, { heading: "", text: "" }];
  }
  function removeTopic(index: number) {
    if (!draft) return;
    draft.topics = draft.topics.filter((_, i) => i !== index);
  }

  const canWrite = $derived(meeting.can_write);
  const reviewing = $derived(meeting.status === "review" && canWrite);
  const hasTranscript = $derived(segments.length > 0 || !!meeting.transcript_text);
  // The document exists once there is something to print: minutes, or at least the words.
  const exportable = $derived(
    !inFlight(meeting.status) &&
      meeting.status !== "recording" &&
      (!!meeting.minutes || hasTranscript),
  );
  // The AI box: a colleague's own words over this meeting, applied as them (off means
  // invisible, #126 — the host draws it only where the press can work).
  const canRevise = $derived(
    canWrite &&
      aiEnabled(page.data.user, "meeting_assist") &&
      !inFlight(meeting.status) &&
      meeting.status !== "recording",
  );

  /**
   * Before the model reads the meeting, the reviewer's unsaved edits are saved — the box must
   * change what the reader sees, not the draft as it was ten keystrokes ago.
   */
  async function saveDraftFirst(): Promise<boolean> {
    if (!reviewing || !draft) return true;
    const res = await fetch(`/api/v1/meetings/${meeting.id}/minutes`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: payload,
    });
    return res.ok;
  }
  /** The row was rewritten under the page: re-read it and re-seed the copies the editors hold. */
  async function onRevised(): Promise<void> {
    draftFor = null;
    participantsFor = null;
    await invalidate("meetings:meeting");
  }

  /**
   * Can *this* browser play the recording? Safari on iOS plays no WebM, which is what every
   * Chrome-made recording is — and a dead player looks exactly like a broken one. Asked of the
   * element itself, after mount; a "maybe" counts as yes (the element decides on play).
   */
  let audioPlayable = $state(true);
  $effect(() => {
    const type = meeting.audio_content_type;
    if (!type || typeof document === "undefined") {
      audioPlayable = true;
      return;
    }
    const probe = document.createElement("audio");
    audioPlayable = probe.canPlayType(type) !== "";
  });
  const audioTypeLabel = $derived((meeting.audio_content_type ?? "").split("/")[1] ?? "");
  const inputClass =
    "w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";
  const smallInput = `${inputClass} py-1.5`;
</script>

{#snippet actionItem(i: number)}
  {#if draft}
    {@const item = draft.action_items[i]}
    <div class="rounded-lg border border-border p-3">
      <div class="flex items-center gap-2">
        <input
          class={smallInput}
          bind:value={item.title}
          placeholder={t("meetings.review.item_placeholder")}
          disabled={!reviewing}
        />
        {#if reviewing}
          <button
            type="button"
            class="shrink-0 rounded p-1 text-text-muted hover:text-red-600"
            aria-label={t("common.remove")}
            onclick={() => removeItem(i)}><X size={14} /></button
          >
        {/if}
      </div>
      <div class="mt-2 grid gap-2 sm:grid-cols-2">
        <div>
          <span class="mb-1 block text-xs text-text-muted">{t("meetings.review.owner")}</span>
          <Combobox
            id={`item-owner-${i}`}
            name={`_owner_${i}`}
            items={ownerItems}
            value={ownerValue(item)}
            placeholder={t("meetings.review.nobody")}
            onselect={(value: string) => setOwner(item, value)}
            oncreate={(name: string) => setOwner(item, `n:${name.trim()}`)}
          />
        </div>
        <div>
          <label for={`item-due-${i}`} class="mb-1 block text-xs text-text-muted"
            >{t("meetings.review.due")}</label
          >
          <DateInput
            id={`item-due-${i}`}
            name={`_due_${i}`}
            value={item.due_date ?? ""}
            onchange={(value: string) => (item.due_date = value || null)}
          />
        </div>
      </div>
      <label class="mt-2 flex items-center gap-2 text-sm text-text">
        <input
          type="checkbox"
          class="size-4 rounded border-border"
          bind:checked={item.create_task}
          disabled={!reviewing}
        />
        {item.owner_contact_id
          ? t("meetings.review.create_task_contact")
          : t("meetings.review.create_task")}
      </label>
      {@render evidence(item)}
    </div>
  {/if}
{/snippet}

{#snippet evidence(item: MinutesDecision | MinutesActionItem)}
  {#if item.quote}
    <p class="mt-1 text-xs text-text-muted">
      {#if !item.verified}
        <span
          class="mr-1 inline-flex items-center gap-1 rounded-md bg-amber-50 px-1.5 py-0.5 text-amber-800 dark:bg-amber-950 dark:text-amber-200"
          title={t("meetings.review.unverified_hint")}
        >
          <AlertTriangle size={11} />
          {t("meetings.review.unverified")}
        </span>
      {/if}
      {#if item.at != null}<span class="tabular-nums">{fmtClock(item.at)}</span> ·
      {/if}
      <span class="italic">“{item.quote}”</span>
    </p>
  {:else if item.quote === null && item.verified === false}
    <p class="mt-1 text-xs text-amber-800 dark:text-amber-200">{t("meetings.review.unverified")}</p>
  {/if}
{/snippet}

<svelte:head>
  <title>{pageTitle(meeting.title)}</title>
</svelte:head>

<a
  href={returnHref("/meetings")}
  class="mb-4 inline-flex items-center gap-1.5 text-sm text-text-muted hover:text-text"
>
  <ArrowLeft size={15} />
  {t("nav.meetings")}
</a>

<div class="mb-6 flex flex-wrap items-start justify-between gap-4">
  <div class="min-w-0">
    <div class="flex flex-wrap items-center gap-2">
      <h1 class="text-xl font-semibold text-text">{meeting.title}</h1>
      <MeetingStatusPill status={meeting.status} />
    </div>
    <p class="mt-1 text-sm text-text-muted">
      {fmtDateTime(meeting.occurred_at)}
      · {kindLabel(meeting.kind)}
      · {sourceLabel(meeting.source)}
      {#if meeting.duration_seconds}· {fmtDuration(meeting.duration_seconds)}{/if}
      {#if meeting.company_name}
        · <a href={`/companies/${meeting.company_id}`} class="hover:text-text hover:underline"
          >{meeting.company_name}</a
        >
      {/if}
      {#if meeting.project_name}· {meeting.project_name}{/if}
      {#if meeting.owner_name}· {meeting.owner_name}{/if}
    </p>
  </div>
  <div class="flex flex-wrap items-center gap-2">
    {#if exportable}
      <Button type="button" variant="secondary" onclick={() => (exportOpen = true)}>
        <Download size={15} />
        {t("meetings.export.button")}
      </Button>
    {/if}
    {#if meeting.status === "failed" && canWrite}
      <form method="POST" action="?/retry" use:enhance={busy.keep("retry")}>
        <Button type="submit" variant="secondary" loading={busy.is("retry")}>
          <RefreshCw size={15} />
          {t("meetings.action.retry")}
        </Button>
      </form>
    {/if}
    {#if canWrite || meeting.can_delete}
      <ActionsMenu
        label={t("common.actions")}
        items={[
          ...(meeting.audio_file_id && canWrite
            ? [{ label: t("meetings.action.delete_audio"), onclick: () => (confirmAudio = true) }]
            : []),
          ...(meeting.can_delete
            ? [
                {
                  label: t("common.delete"),
                  onclick: () => (confirmDelete = true),
                  danger: true,
                },
              ]
            : []),
        ]}
      />
    {/if}
  </div>
</div>

{#if form?.error}
  <p
    class="mb-4 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950 dark:text-red-300"
    role="alert"
  >
    {t(form.error)}
  </p>
{:else if form?.confirmed}
  <p class="mb-4 rounded-lg bg-surface px-4 py-3 text-sm text-text">
    {t("meetings.review.confirmed")}
    {#if form.skipped?.length}
      <span class="block text-amber-800 dark:text-amber-200">
        {t("meetings.review.tasks_skipped", { count: String(form.skipped.length) })}
        {#each form.skipped as skipped (skipped.title)}
          · {skipped.title}{/each}
      </span>
    {/if}
  </p>
{/if}

{#if inFlight(meeting.status) || meeting.status === "recording"}
  <Card kind="panel">
    <div class="flex items-center gap-3">
      <Sparkles size={18} class="text-brand" />
      <div>
        <p class="text-sm font-medium text-text" aria-live="polite">
          {t(`meetings.progress.${meeting.status}`)}
        </p>
        <p class="text-xs text-text-muted">{t("meetings.progress.hint")}</p>
      </div>
    </div>
  </Card>
{:else if meeting.status === "failed"}
  <Card kind="panel">
    <div class="flex items-start gap-3">
      <AlertTriangle size={18} class="mt-0.5 text-red-600 dark:text-red-400" />
      <div>
        <p class="text-sm font-medium text-text">{t("meetings.progress.failed")}</p>
        {#if meeting.error_key}
          <p class="text-sm text-text-muted">{t(meeting.error_key)}</p>
        {/if}
      </div>
    </div>
  </Card>
{/if}

{#if meeting.status === "done" && meeting.minutes}
  <!-- The record: the minutes as confirmed, and where they went. -->
  <div class="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,380px)]">
    <div class="space-y-6">
      <Card kind="panel" title={t("meetings.review.minutes")}>
        {#if meeting.interaction_id || taskIds.length}
          <p class="mb-4 text-sm text-text-muted">
            {#if meeting.interaction_id}
              <a
                href={`/interactions?interaction=${meeting.interaction_id}`}
                class="text-brand hover:underline">{t("meetings.review.open_interaction")}</a
              >
            {/if}
            {#if taskIds.length}
              · {tn("meetings.review.tasks_created", taskIds.length)}
              {#each taskIds as taskId, i (taskId)}
                <a href={`/tasks/${taskId}`} class="mr-1 text-brand hover:underline">#{i + 1}</a>
              {/each}
            {/if}
          </p>
        {/if}
        {#if meeting.minutes.summary}
          <Markdown value={meeting.minutes.summary} class="text-sm" />
        {/if}
        {#each meeting.minutes.topics ?? [] as topic (topic.heading)}
          <h3 class="mt-4 text-sm font-semibold text-text">{topic.heading}</h3>
          <Markdown value={topic.text} class="text-sm" />
        {/each}
        {#if meeting.minutes.decisions?.length}
          <h3 class="mt-4 text-sm font-semibold text-text">{t("meetings.review.decisions")}</h3>
          <ul class="mt-1 list-disc space-y-1 pl-5 text-sm text-text">
            {#each meeting.minutes.decisions ?? [] as decision, i (i)}
              <li>
                {decision.text}
                {#if decision.at != null}<span class="text-xs text-text-muted tabular-nums"
                    >({fmtClock(decision.at)})</span
                  >{/if}
              </li>
            {/each}
          </ul>
        {/if}
        {#if meeting.minutes.action_items?.length}
          <h3 class="mt-4 text-sm font-semibold text-text">{t("meetings.review.action_items")}</h3>
          {#each doneGroups as block (block.side)}
            <p class="mt-2 text-xs font-medium tracking-wide text-text-muted uppercase">
              {t(`meetings.minutes.side_${block.side}`)}
            </p>
            {#each block.groups as group (group.key)}
              {#if group.name}<p class="mt-1 text-sm font-medium text-text">{group.name}</p>{/if}
              <ul class="mt-0.5 list-disc space-y-1 pl-5 text-sm text-text">
                {#each group.indices as i (i)}
                  {@const item = (meeting.minutes.action_items ?? [])[i]}
                  <li>
                    {item.title}
                    {#if item.due_date}<span class="text-text-muted">— {item.due_date}</span>{/if}
                  </li>
                {/each}
              </ul>
            {/each}
          {/each}
        {/if}
        {#if meeting.minutes.open_questions?.length}
          <h3 class="mt-4 text-sm font-semibold text-text">
            {t("meetings.review.open_questions")}
          </h3>
          <ul class="mt-1 list-disc space-y-1 pl-5 text-sm text-text">
            {#each meeting.minutes.open_questions ?? [] as question, i (i)}
              <li>{question}</li>
            {/each}
          </ul>
        {/if}
      </Card>
      {#if canRevise}
        <MeetingAIRevise meetingId={meeting.id} onapplied={onRevised} />
      {/if}
    </div>
    <div class="space-y-6">
      {@render transcriptPanel(false)}
    </div>
  </div>
{:else if meeting.status === "review" && draft}
  <div class="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,420px)]">
    <!-- The draft, every line editable, every claim with its evidence. -->
    <form
      id="minutes-form"
      method="POST"
      action="?/saveMinutes"
      class="space-y-6"
      use:enhance={busy.keep("save")}
    >
      <input type="hidden" name="minutes" value={payload} />

      {#if draft.truncated || draft.partial_input}
        <p
          class="rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-200"
        >
          {t(draft.partial_input ? "meetings.review.partial_input" : "meetings.review.truncated")}
        </p>
      {/if}

      <Card kind="panel" title={t("meetings.review.minutes")}>
        <div class="space-y-4">
          <div>
            <label for="minutes-title" class="mb-1 block text-xs font-medium text-text-muted"
              >{t("meetings.field.title")}</label
            >
            <input
              id="minutes-title"
              class={inputClass}
              value={draft.title ?? meeting.title}
              oninput={(e) => draft && (draft.title = (e.currentTarget as HTMLInputElement).value)}
              disabled={!reviewing}
            />
          </div>
          <div>
            <label for="minutes-summary" class="mb-1 block text-xs font-medium text-text-muted"
              >{t("meetings.review.summary")}</label
            >
            <textarea
              id="minutes-summary"
              rows="5"
              class="{inputClass} resize-y"
              bind:value={draft.summary}
              disabled={!reviewing}></textarea>
          </div>
        </div>
      </Card>

      <Card kind="panel" title={t("meetings.review.topics")}>
        <div class="space-y-4">
          {#each draft.topics as topic, i (i)}
            <div class="rounded-lg border border-border p-3">
              <div class="flex items-center gap-2">
                <input
                  class={smallInput}
                  placeholder={t("meetings.review.topic_heading")}
                  bind:value={topic.heading}
                  disabled={!reviewing}
                />
                {#if reviewing}
                  <button
                    type="button"
                    class="shrink-0 rounded p-1 text-text-muted hover:text-red-600"
                    aria-label={t("common.remove")}
                    onclick={() => removeTopic(i)}><X size={14} /></button
                  >
                {/if}
              </div>
              <textarea
                rows="3"
                class="{inputClass} mt-2 resize-y"
                bind:value={topic.text}
                disabled={!reviewing}></textarea>
            </div>
          {/each}
          {#if reviewing}
            <Button type="button" variant="secondary" size="sm" onclick={addTopic}>
              <Plus size={14} />
              {t("meetings.review.add_topic")}
            </Button>
          {/if}
        </div>
      </Card>

      <Card kind="panel" title={t("meetings.review.decisions")}>
        <div class="space-y-3">
          {#each draft.decisions as decision, i (i)}
            <div class="rounded-lg border border-border p-3">
              <div class="flex items-center gap-2">
                <input
                  class={smallInput}
                  bind:value={decision.text}
                  placeholder={t("meetings.review.decision_placeholder")}
                  disabled={!reviewing}
                />
                {#if reviewing}
                  <button
                    type="button"
                    class="shrink-0 rounded p-1 text-text-muted hover:text-red-600"
                    aria-label={t("common.remove")}
                    onclick={() => removeDecision(i)}><X size={14} /></button
                  >
                {/if}
              </div>
              {@render evidence(decision)}
            </div>
          {:else}
            <p class="text-sm text-text-muted">{t("meetings.review.no_decisions")}</p>
          {/each}
          {#if reviewing}
            <Button type="button" variant="secondary" size="sm" onclick={addDecision}>
              <Plus size={14} />
              {t("meetings.review.add_decision")}
            </Button>
          {/if}
        </div>
      </Card>

      <Card kind="panel" title={t("meetings.review.action_items")}>
        <p class="mb-3 text-xs text-text-muted">{t("meetings.review.action_items_hint")}</p>
        <div class="space-y-4">
          {#each draftGroups as block (block.side)}
            <div>
              <p class="mb-1 text-xs font-medium tracking-wide text-text-muted uppercase">
                {t(`meetings.minutes.side_${block.side}`)}
              </p>
              <div class="space-y-3">
                {#each block.groups as group (group.key)}
                  {#if group.name && group.indices.length > 1}
                    <p class="text-sm font-medium text-text">{group.name}</p>
                  {/if}
                  {#each group.indices as i (i)}
                    {@render actionItem(i)}
                  {/each}
                {/each}
              </div>
            </div>
          {:else}
            <p class="text-sm text-text-muted">{t("meetings.review.no_action_items")}</p>
          {/each}
          {#if reviewing}
            <Button type="button" variant="secondary" size="sm" onclick={addItem}>
              <Plus size={14} />
              {t("meetings.review.add_item")}
            </Button>
          {/if}
        </div>
      </Card>

      <Card kind="panel" title={t("meetings.review.open_questions")}>
        <div class="space-y-2">
          {#each draft.open_questions as _question, i (i)}
            <div class="flex items-center gap-2">
              <input
                class={smallInput}
                bind:value={draft.open_questions[i]}
                disabled={!reviewing}
              />
              {#if reviewing}
                <button
                  type="button"
                  class="shrink-0 rounded p-1 text-text-muted hover:text-red-600"
                  aria-label={t("common.remove")}
                  onclick={() => removeQuestion(i)}><X size={14} /></button
                >
              {/if}
            </div>
          {:else}
            <p class="text-sm text-text-muted">{t("meetings.review.no_questions")}</p>
          {/each}
          {#if reviewing}
            <Button type="button" variant="secondary" size="sm" onclick={addQuestion}>
              <Plus size={14} />
              {t("meetings.review.add_question")}
            </Button>
          {/if}
        </div>
      </Card>

      {#if reviewing}
        <div class="flex flex-wrap items-center gap-3">
          <Button
            type="submit"
            variant="secondary"
            loading={busy.is("save")}
            disabled={busy.active}
          >
            {t("meetings.review.save_draft")}
          </Button>
          <Button type="button" onclick={() => (confirmOpen = true)} disabled={busy.active}>
            <Check size={15} />
            {t("meetings.review.confirm")}
          </Button>
          {#if unverified > 0}
            <span class="text-xs text-amber-800 dark:text-amber-200">
              {t("meetings.review.unverified_count", { count: String(unverified) })}
            </span>
          {/if}
        </div>
      {/if}
    </form>

    <div class="space-y-6">
      {#if reviewing}
        <Card kind="panel" title={t("meetings.review.filing")}>
          <form method="POST" action="?/update" class="space-y-3" use:enhance={busy.keep("filing")}>
            <div>
              <label for="filing-company" class="mb-1 block text-xs text-text-muted"
                >{t("meetings.field.client")}</label
              >
              <Combobox
                id="filing-company"
                name="company_id"
                value={meeting.company_id ?? ""}
                items={companyPicker.live}
                archived={companyPicker.retired}
                archivedLabel={companyArchivedLabel()}
                placeholder={t("meetings.field.client_placeholder")}
              />
            </div>
            <div>
              <label for="filing-project" class="mb-1 block text-xs text-text-muted"
                >{t("meetings.field.project")}</label
              >
              <Combobox
                id="filing-project"
                name="project_id"
                value={meeting.project_id ?? ""}
                items={projectPicker.live}
                archived={projectPicker.retired}
                archivedLabel={projectArchivedLabel()}
                placeholder={t("meetings.field.project_placeholder")}
              />
            </div>
            <div>
              <label for="filing-kind" class="mb-1 block text-xs text-text-muted"
                >{t("meetings.field.kind")}</label
              >
              <select id="filing-kind" name="kind" class={smallInput} value={meeting.kind}>
                <option value="physical">{kindLabel("physical")}</option>
                <option value="online">{kindLabel("online")}</option>
              </select>
            </div>
            <Button type="submit" variant="secondary" size="sm" loading={busy.is("filing")}>
              {t("common.save")}
            </Button>
          </form>
        </Card>
      {/if}
      {#if canRevise}
        <MeetingAIRevise meetingId={meeting.id} before={saveDraftFirst} onapplied={onRevised} />
      {/if}
      {@render transcriptPanel(reviewing)}
    </div>
  </div>
{:else if meeting.status === "failed" || (meeting.status === "review" && !draft)}
  <div class="mt-6">{@render transcriptPanel(false)}</div>
{/if}

{#snippet transcriptPanel(editable: boolean)}
  <!-- Redraft rides the participants form: `formaction` picks the action, and the roster in
       the hidden field travels with it so the redraft reads what the screen shows. -->
  {#if meeting.audio_file_id}
    <Card kind="panel" title={t("meetings.review.recording")}>
      {#if audioPlayable}
        <!-- `metadata`, not `none`: a phone's player shows its length before the first tap,
             and the API answers byte ranges, so the read is a few kilobytes. -->
        <audio controls preload="metadata" class="w-full">
          <source
            src={`/api/v1/files/${meeting.audio_file_id}`}
            type={meeting.audio_content_type ?? undefined}
          />
        </audio>
      {:else}
        <p class="text-sm text-text">
          {t("meetings.review.audio_unplayable", { type: audioTypeLabel })}
        </p>
      {/if}
      <p class="mt-2 text-xs text-text-muted">
        {t("meetings.review.recording_hint")}
        <a
          href={`/api/v1/files/${meeting.audio_file_id}`}
          download
          class="ml-1 text-brand hover:underline"
          data-sveltekit-reload>{t("meetings.review.download_audio")}</a
        >
      </p>
    </Card>
  {/if}
  {#if segments.length || meeting.transcript_text}
    <Card kind="panel" title={t("meetings.review.transcript")}>
      {#if undiarized}
        <p
          class="mb-4 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-200"
        >
          {t("meetings.review.undiarized", { model: meeting.transcript_model ?? "?" })}
        </p>
      {/if}
      <form
        method="POST"
        action="?/participants"
        class="mb-4"
        use:enhance={busy.keep("participants")}
      >
        <input type="hidden" name="participants" value={participantsPayload} />
        <p class="mb-2 text-xs text-text-muted">
          {t(
            speakerLabels.length
              ? "meetings.review.participants_hint"
              : "meetings.review.participants_hint_plain",
          )}
          {#if meeting.transcript_parts > 1}
            {t("meetings.review.speakers_parts", { parts: String(meeting.transcript_parts) })}
          {/if}
        </p>
        <ParticipantsEditor
          bind:participants
          {members}
          companyId={meeting.company_id ?? ""}
          companyName={meeting.company_name ?? null}
          {speakerLabels}
          {editable}
          definitions={data.contactDefinitions}
          locale={data.locale}
          created={form?.inlineCreated ?? null}
          qcError={form?.qcError ?? null}
        />
        {#if editable}
          <div class="mt-2 flex flex-wrap items-center gap-2">
            <Button
              type="submit"
              variant="secondary"
              size="sm"
              loading={busy.is("participants")}
              disabled={!participantsDirty || busy.active}
            >
              {t("meetings.review.save_participants")}
            </Button>
            {#if meeting.status === "review"}
              <Button
                type="submit"
                variant="secondary"
                size="sm"
                formaction="?/redraft"
                title={t("meetings.review.redraft_hint")}
                loading={busy.is("redraft")}
                disabled={busy.active}
              >
                <RefreshCw size={14} />
                {t("meetings.review.redraft")}
              </Button>
            {/if}
          </div>
        {/if}
      </form>
      <div class="max-h-[70vh] space-y-2 overflow-y-auto text-sm">
        {#each segments as segment, i (i)}
          <p>
            <span class="mr-2 font-mono text-xs text-text-muted tabular-nums"
              >{fmtClock(segment.start)}</span
            >
            {#if segment.speaker}
              <span class="font-medium text-text">{speakerName(segment.speaker)}:</span>
            {/if}
            <span class="text-text">{segment.text}</span>
          </p>
        {:else}
          <p class="whitespace-pre-wrap text-text">{meeting.transcript_text}</p>
        {/each}
      </div>
    </Card>
  {/if}
{/snippet}

<ConfirmDialog
  bind:open={confirmOpen}
  title={t("meetings.review.confirm_title")}
  message={t("meetings.review.confirm_message")}
  consequences={[
    t("meetings.review.confirm_interaction", { kind: kindLabel(meeting.kind) }),
    tn("meetings.review.confirm_tasks", tasksToCreate),
  ]}
  action="?/confirm"
  fields={{ minutes: payload }}
  confirmLabel={t("meetings.review.confirm")}
  variant="primary"
  onsuccess={() => (confirmOpen = false)}
/>

<MeetingExportDialog
  bind:open={exportOpen}
  meetingId={meeting.id}
  defaults={meeting.document_sections ?? []}
  {hasTranscript}
/>

<ConfirmDialog
  bind:open={confirmAudio}
  title={t("meetings.action.delete_audio")}
  message={t("meetings.action.delete_audio_message")}
  action="?/deleteAudio"
  confirmLabel={t("meetings.action.delete_audio")}
/>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("meetings.action.delete_title")}
  message={t("meetings.action.delete_message")}
  consequences={[t("meetings.action.delete_keeps")]}
  action="?/delete"
  confirmLabel={t("common.delete")}
/>

{#if !canWrite && meeting.status === "review"}
  <p class="mt-4 text-xs text-text-muted">{t("meetings.review.read_only")}</p>
{/if}
