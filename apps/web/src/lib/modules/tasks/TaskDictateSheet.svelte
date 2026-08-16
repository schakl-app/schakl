<script lang="ts">
  /**
   * Speak a task (#382) — record, read the words back, review the draft, create.
   *
   * Four rules hold this surface up, and each of them is a bug if it is broken.
   *
   * **The transcript stays visible and editable, and the parse is not a second button.**
   * docs/VOICE.md requires the words be readable before they are parsed, and the reason it
   * gives is *correctability*: Dutch proper nouns are the weak link and a misheard client name
   * is only fixable while the words are still on screen. That is a rule about the textarea, not
   * about the click — so the parse runs on its own and "Opnieuw verwerken" re-runs it after an
   * edit. The correction surface is kept; the extra click is not.
   *
   * **Speaking again appends.** People dictate in breaths ("…en zet hem op vrijdag"), and a
   * recorder that discards the first forty seconds on the second press is one nobody presses
   * twice. The /time quick-add already does this; it is the same line.
   *
   * **A parse that yields nothing must not throw the words away.** An empty answer still opens
   * the review, with the transcript as the title and a line saying so. Losing a spoken minute
   * is the worst outcome available and looks exactly like the feature being broken.
   *
   * **Nothing is written until Aanmaken.** Which is also *why* the draft may carry the whole
   * form: #327's narrow vocabulary answers an untrusted email applied by a worker nobody is
   * watching, and neither half is true here (see `core/ai/taskdraft.py`).
   *
   * Tier 3 (docs/UX.md): a `SlideOver`, not a `Modal` — fourteen fields and a checklist in a
   * 512 px centred dialog is the 1445 px-tall client editor #364 already fixed once.
   */
  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { Mic, Plus, Sparkles, Trash2, X } from "@lucide/svelte";
  import { onMount } from "svelte";

  import { aiEnabled } from "$lib/core/ai";
  import { fmtDayMonth } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { memberArchivedLabel, splitMemberOptions } from "$lib/core/members";
  import { can } from "$lib/core/permissions";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import DurationInput from "$lib/core/ui/DurationInput.svelte";
  import SlideOver from "$lib/core/ui/SlideOver.svelte";
  import { MAX_TASK_RECORD_MS, Recorder, VoiceButton, recordingSupported } from "$lib/core/voice";
  import { formatMinutes } from "$lib/modules/time/format";

  interface Lookup {
    id: string;
    name?: string | null;
    title?: string | null;
  }
  interface StatusDef {
    key: string;
    name: string;
  }
  interface LabelDef {
    id: string;
    name: string;
  }
  interface Member {
    user_id: string;
    full_name: string | null;
    email: string | null;
    is_active?: boolean;
  }
  interface ChecklistItem {
    title: string;
    description?: string | null;
  }
  interface LinkItem {
    url: string;
    title?: string | null;
  }
  interface Draft {
    title: string | null;
    description: string | null;
    due_date: string | null;
    priority: string | null;
    status: string | null;
    company_id: string | null;
    project_id: string | null;
    assignee_user_id: string | null;
    label_ids: string[];
    allocated_minutes: number | null;
    checklist_title: string | null;
    checklist_items: ChecklistItem[];
    links: LinkItem[];
    requires_interaction: boolean | null;
    visible_to_client: boolean | null;
    truncated: boolean;
  }

  let {
    open = $bindable(false),
    companies = [],
    projects = [],
    labels = [],
    statuses = [],
    members = [],
    companyId = null,
    projectId = null,
    action = "?/createDictated",
  }: {
    open?: boolean;
    companies?: Lookup[];
    projects?: (Lookup & { company_id?: string | null })[];
    labels?: LabelDef[];
    statuses?: StatusDef[];
    members?: Member[];
    /** What the surface already knows — a default the spoken words override, never a filter. */
    companyId?: string | null;
    projectId?: string | null;
    action?: string;
  } = $props();

  const busy = new InFlight();
  const recorder = new Recorder(MAX_TASK_RECORD_MS);

  let transcript = $state("");
  let draft = $state<Draft | null>(null);
  let status = $state<string | null>(null);
  let error = $state<string | null>(null);
  let budgetReached = $state(false);
  /** True when the parse answered nothing — the words survive as the title and this says why. */
  let parseWasEmpty = $state(false);
  /** Which fields schakl filled in, so the form can mark them (see `marked` below). A plain
   *  array rather than a Set: it is replaced wholesale on every parse, never mutated. */
  let filled = $state<string[]>([]);
  let micSupported = $state(false);

  onMount(() => {
    micSupported = recordingSupported();
    return () => recorder.abort();
  });

  // Three conditions and every one is load-bearing: the org has a provider that can transcribe
  // at all (`speech`, resolved server-side), this browser can record, and the caller could
  // actually create the task the draft is for. A control that can only refuse is not drawn.
  const canDictate = $derived(
    aiEnabled(page.data.user, "task_assist") &&
      aiEnabled(page.data.user, "speech") &&
      micSupported &&
      can(page.data.user, "tasks.task.create"),
  );

  const memberPicker = $derived(splitMemberOptions(members));
  const companyItems = $derived(companies.map((c) => ({ value: c.id, label: c.name ?? "" })));
  const projectItems = $derived(
    projects
      .filter((p) => !draft?.company_id || !p.company_id || p.company_id === draft.company_id)
      .map((p) => ({ value: p.id, label: p.name ?? "" })),
  );
  const statusItems = $derived(statuses.map((s) => ({ value: s.key, label: s.name })));
  const priorityItems = $derived(
    (["low", "normal", "high"] as const).map((key) => ({
      value: key,
      label: t(`tasks.priority.${key}`),
    })),
  );

  /**
   * The one-line summary. It is what lets someone confirm a draft without reading fourteen
   * controls — the /time quick-add's line, on a surface with far more to say.
   */
  const summary = $derived.by(() => {
    if (!draft) return "";
    const parts: string[] = [];
    if (draft.due_date) parts.push(fmtDayMonth(draft.due_date));
    if (draft.priority && draft.priority !== "normal")
      parts.push(t(`tasks.priority.${draft.priority}`));
    const company = companies.find((c) => c.id === draft?.company_id);
    if (company?.name) parts.push(company.name);
    const project = projects.find((p) => p.id === draft?.project_id);
    if (project?.name) parts.push(project.name);
    const member = members.find((m) => m.user_id === draft?.assignee_user_id);
    if (member) parts.push(member.full_name || member.email || "");
    // ICU plurals compile to garbage in this Paraglide setup, so a count that varies takes a
    // `_one` key and a ternary rather than a `{n, plural, …}` block.
    if (draft.checklist_items.length)
      parts.push(
        draft.checklist_items.length === 1
          ? t("tasks.dictate.steps_count_one")
          : t("tasks.dictate.steps_count", { count: draft.checklist_items.length }),
      );
    if (draft.allocated_minutes) parts.push(formatMinutes(draft.allocated_minutes));
    return parts.filter(Boolean).join(" · ");
  });

  function reset() {
    transcript = "";
    draft = null;
    status = null;
    error = null;
    budgetReached = false;
    parseWasEmpty = false;
    filled = [];
  }

  function close() {
    recorder.abort();
    reset();
    open = false;
  }

  /**
   * Every way out releases the microphone — not only the Annuleren that calls `close()`.
   *
   * Found in a browser and not in review: `SlideOver` owns three of its four exits (the ✕, the
   * backdrop and Escape) and closes by writing `open` itself, so a handler on our own button
   * covers exactly one of them. Dismissing the sheet mid-sentence left the capture running, the
   * elapsed counter climbing behind a closed panel and the browser's recording indicator lit —
   * which is #246's stated privacy rule, broken by the one thing that surface cannot see.
   *
   * Watching `open` is the fix precisely because it is the only thing all four exits agree on.
   */
  $effect(() => {
    if (!open) {
      recorder.abort();
      reset();
    }
  });

  async function post(path: string, body: unknown): Promise<Record<string, unknown> | null> {
    const res = await fetch(path, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const payload = await res.json().catch(() => null);
      if (payload?.error?.code === "ai_budget_reached") budgetReached = true;
      else error = payload?.error?.message ?? "errors.ai_provider_error";
      return null;
    }
    return await res.json();
  }

  async function dictate() {
    error = null;
    budgetReached = false;
    const audio = await recorder.start();
    if (recorder.error) {
      error = recorder.error;
      return;
    }
    if (!audio) return; // aborted, or nothing captured
    status = "voice.transcribing";
    try {
      const body = await post("/ai/tasks/transcribe", {
        audio,
        language: page.data.locale ?? "nl",
      });
      const heard = String(body?.text ?? "").trim();
      if (!body) return;
      if (!heard) {
        error = "voice.error_no_speech";
        return;
      }
      // Append: a second breath adds to the first, it does not replace it.
      transcript = transcript.trim() ? `${transcript.trim()} ${heard}` : heard;
    } finally {
      status = null;
    }
    await parse();
  }

  async function parse(override = false) {
    if (!transcript.trim()) return;
    error = null;
    parseWasEmpty = false;
    status = "tasks.dictate.thinking";
    try {
      const body = await post("/ai/tasks/parse", {
        text: transcript.trim(),
        company_id: companyId,
        project_id: projectId,
        override_budget: override,
      });
      if (!body) return;
      const answered = body as unknown as Draft;
      // Which fields the model actually filled — captured *before* the fallbacks below, so the
      // marker says "schakl chose this" and never "this happens to have a value".
      const marks: string[] = [];
      for (const key of [
        "title",
        "description",
        "due_date",
        "priority",
        "status",
        "company_id",
        "project_id",
        "assignee_user_id",
        "allocated_minutes",
        "requires_interaction",
        "visible_to_client",
      ] as const) {
        if (answered[key] !== null && answered[key] !== undefined) marks.push(key);
      }
      if (answered.label_ids?.length) marks.push("label_ids");
      if (answered.checklist_items?.length) marks.push("checklist");
      filled = marks;
      // Nothing usable came back — but the words are still good, so they become the title
      // rather than being thrown away, and the sheet says which of the two happened.
      parseWasEmpty = marks.length === 0;
      draft = {
        ...answered,
        title: answered.title || transcript.trim().slice(0, 512),
        label_ids: answered.label_ids ?? [],
        checklist_items: answered.checklist_items ?? [],
        links: answered.links ?? [],
      };
    } finally {
      status = null;
    }
  }

  /** A ✦ beside a control the model filled in — so "schakl picked this client" and "I picked
   *  this client" are not the same-looking cell, which is what makes a wrong one a two-second
   *  fix instead of something noticed next week. */
  function marked(key: string): boolean {
    return filled.includes(key);
  }

  function toggleLabel(id: string) {
    if (!draft) return;
    draft.label_ids = draft.label_ids.includes(id)
      ? draft.label_ids.filter((x) => x !== id)
      : [...draft.label_ids, id];
  }

  function addStep() {
    if (!draft) return;
    draft.checklist_items = [...draft.checklist_items, { title: "" }];
  }

  function removeStep(index: number) {
    if (!draft) return;
    draft.checklist_items = draft.checklist_items.filter((_, i) => i !== index);
  }

  function removeLink(index: number) {
    if (!draft) return;
    draft.links = draft.links.filter((_, i) => i !== index);
  }

  /** The whole draft as one field. The shape is nested (steps, links, labels) and a form
   *  cannot express that in flat inputs without inventing a naming convention the action then
   *  has to re-parse — so the action reads one JSON payload and posts one API call. */
  const payload = $derived(
    draft
      ? JSON.stringify({
          ...draft,
          checklist_items: draft.checklist_items.filter((i) => i.title.trim()),
          links: draft.links.filter((l) => l.url.trim()),
        })
      : "",
  );

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<SlideOver bind:open title={t("tasks.dictate.title")} size="2xl">
  {#if !canDictate}
    <!-- Reached only if the host drew the opener without the same gate; the sheet says what is
         missing rather than presenting an inert microphone. -->
    <p class="text-sm text-muted">{t("tasks.dictate.unavailable")}</p>
  {:else}
    <div class="space-y-5">
      <!-- 1. The transcript. Editable, and re-parsable after an edit (docs/VOICE.md). -->
      <section class="space-y-2">
        <label for="dictate-transcript" class="block text-sm font-medium text-text">
          {t("tasks.dictate.transcript")}
        </label>
        <textarea
          id="dictate-transcript"
          bind:value={transcript}
          rows="3"
          placeholder={t("tasks.dictate.placeholder")}
          class="{inputClass} resize-y"></textarea>
        <div class="flex flex-wrap items-center gap-2">
          <VoiceButton
            {recorder}
            onstart={dictate}
            onstop={() => recorder.stop()}
            disabled={status !== null || busy.active}
          />
          <Button
            type="button"
            variant="secondary"
            disabled={!transcript.trim() || status !== null}
            loading={status === "tasks.dictate.thinking"}
            onclick={() => parse()}
          >
            <Sparkles size={14} />
            {draft ? t("tasks.dictate.reparse") : t("tasks.dictate.parse")}
          </Button>
          <!-- Never colour alone (docs/UX.md): the in-flight state is words, in a live region. -->
          <span class="text-sm text-muted" aria-live="polite">
            {status ? t(status) : ""}
          </span>
        </div>
        {#if error}
          <p class="text-sm text-red-600 dark:text-red-400">{t(error)}</p>
        {/if}
        {#if budgetReached}
          <div
            class="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm dark:bg-amber-950/30"
          >
            <p class="text-text">{t("ai.budget_notice")}</p>
            <button
              type="button"
              class="mt-2 text-sm font-medium text-brand underline"
              onclick={() => {
                budgetReached = false;
                void parse(true);
              }}
            >
              {t("ai.budget_proceed")}
            </button>
          </div>
        {/if}
      </section>

      {#if draft}
        <!-- 2. What was understood, in one line. -->
        <section class="rounded-lg border border-border bg-surface-2 p-3">
          <p class="text-xs font-medium uppercase tracking-wide text-muted">
            {t("tasks.dictate.understood")}
          </p>
          <p class="mt-1 text-sm text-text">{summary || t("tasks.dictate.nothing_extra")}</p>
          {#if parseWasEmpty}
            <p class="mt-2 text-sm text-amber-700 dark:text-amber-400">
              {t("tasks.dictate.empty_parse")}
            </p>
          {/if}
          {#if draft.truncated}
            <p class="mt-2 text-sm text-amber-700 dark:text-amber-400">
              {t("tasks.dictate.truncated")}
            </p>
          {/if}
        </section>

        <!-- 3. The form. Nothing is written until Aanmaken. -->
        <form method="POST" {action} use:enhance={busy.clear("")} class="space-y-4">
          <input type="hidden" name="payload" value={payload} />

          <div>
            <label
              for="dictate-title"
              class="mb-1 flex items-center gap-1 text-sm font-medium text-text"
            >
              {t("tasks.field.title")}
              {#if marked("title")}<Sparkles
                  size={12}
                  class="text-brand"
                  aria-label={t("tasks.dictate.by_schakl")}
                />{/if}
            </label>
            <input id="dictate-title" bind:value={draft.title} required class={inputClass} />
          </div>

          <div>
            <label
              for="dictate-desc"
              class="mb-1 flex items-center gap-1 text-sm font-medium text-text"
            >
              {t("tasks.field.description")}
              {#if marked("description")}<Sparkles
                  size={12}
                  class="text-brand"
                  aria-label={t("tasks.dictate.by_schakl")}
                />{/if}
            </label>
            <textarea
              id="dictate-desc"
              value={draft.description ?? ""}
              oninput={(e) => draft && (draft.description = e.currentTarget.value)}
              rows="3"
              class="{inputClass} resize-y"></textarea>
          </div>

          <div class="grid gap-3 sm:grid-cols-2">
            <div>
              <span class="mb-1 flex items-center gap-1 text-sm font-medium text-text">
                {t("tasks.field.due_date")}
                {#if marked("due_date")}<Sparkles size={12} class="text-brand" />{/if}
              </span>
              <DateInput
                name="dictate-due"
                value={draft.due_date ?? ""}
                onchange={(v) => draft && (draft.due_date = v || null)}
              />
            </div>
            <div>
              <span class="mb-1 flex items-center gap-1 text-sm font-medium text-text">
                {t("tasks.field.allocated")}
                {#if marked("allocated_minutes")}<Sparkles size={12} class="text-brand" />{/if}
              </span>
              <DurationInput
                minutes={draft.allocated_minutes}
                onchange={(m) => draft && (draft.allocated_minutes = m)}
                ariaLabel={t("tasks.field.allocated")}
              />
            </div>
            <div>
              <span class="mb-1 flex items-center gap-1 text-sm font-medium text-text">
                {t("tasks.field.priority")}
                {#if marked("priority")}<Sparkles size={12} class="text-brand" />{/if}
              </span>
              <Combobox
                items={priorityItems}
                name="dictate-priority"
                value={draft.priority ?? "normal"}
                onselect={(v) => draft && (draft.priority = v || null)}
              />
            </div>
            <div>
              <span class="mb-1 flex items-center gap-1 text-sm font-medium text-text">
                {t("tasks.field.status")}
                {#if marked("status")}<Sparkles size={12} class="text-brand" />{/if}
              </span>
              <Combobox
                items={statusItems}
                name="dictate-status"
                value={draft.status ?? ""}
                placeholder={t("tasks.dictate.status_default")}
                onselect={(v) => draft && (draft.status = v || null)}
              />
            </div>
            <div>
              <span class="mb-1 flex items-center gap-1 text-sm font-medium text-text">
                {t("tasks.field.company")}
                {#if marked("company_id")}<Sparkles size={12} class="text-brand" />{/if}
              </span>
              <Combobox
                items={companyItems}
                name="dictate-company"
                value={draft.company_id ?? ""}
                placeholder={t("common.none")}
                onselect={(v) => draft && (draft.company_id = v || null)}
              />
            </div>
            <div>
              <span class="mb-1 flex items-center gap-1 text-sm font-medium text-text">
                {t("tasks.field.project")}
                {#if marked("project_id")}<Sparkles size={12} class="text-brand" />{/if}
              </span>
              <Combobox
                items={projectItems}
                name="dictate-project"
                value={draft.project_id ?? ""}
                placeholder={t("common.none")}
                onselect={(v) => draft && (draft.project_id = v || null)}
              />
            </div>
          </div>

          {#if members.length > 0}
            <div>
              <span class="mb-1 flex items-center gap-1 text-sm font-medium text-text">
                {t("tasks.field.assignee")}
                {#if marked("assignee_user_id")}<Sparkles size={12} class="text-brand" />{/if}
              </span>
              <Combobox
                items={memberPicker.live}
                name="dictate-assignee"
                value={draft.assignee_user_id ?? ""}
                placeholder={t("common.none")}
                onselect={(v) => draft && (draft.assignee_user_id = v || null)}
                archived={memberPicker.retired}
                archivedLabel={memberArchivedLabel()}
              />
            </div>
          {/if}

          {#if labels.length > 0}
            <div>
              <span class="mb-1 flex items-center gap-1 text-sm font-medium text-text">
                {t("tasks.field.labels")}
                {#if marked("label_ids")}<Sparkles size={12} class="text-brand" />{/if}
              </span>
              <div class="flex flex-wrap gap-1.5">
                {#each labels as label (label.id)}
                  <button
                    type="button"
                    class="rounded-full border px-2.5 py-1 text-xs {draft.label_ids.includes(
                      label.id,
                    )
                      ? 'border-brand bg-brand/10 font-medium text-brand'
                      : 'border-border text-muted'}"
                    aria-pressed={draft.label_ids.includes(label.id)}
                    onclick={() => toggleLabel(label.id)}
                  >
                    {label.name}
                  </button>
                {/each}
              </div>
            </div>
          {/if}

          <!-- The steps. A dictation's most valuable half and the one nobody retypes. -->
          <div>
            <span class="mb-1 flex items-center gap-1 text-sm font-medium text-text">
              {t("tasks.dictate.checklist")}
              {#if marked("checklist")}<Sparkles size={12} class="text-brand" />{/if}
            </span>
            <input
              value={draft.checklist_title ?? ""}
              oninput={(e) => draft && (draft.checklist_title = e.currentTarget.value || null)}
              placeholder={t("tasks.dictate.checklist_title")}
              class="{inputClass} mb-2"
              aria-label={t("tasks.dictate.checklist_title")}
            />
            <ul class="space-y-1.5">
              {#each draft.checklist_items as item, index (index)}
                <li class="flex items-center gap-2">
                  <input
                    bind:value={item.title}
                    class={inputClass}
                    aria-label={t("tasks.dictate.step_n", { n: index + 1 })}
                  />
                  <button
                    type="button"
                    class="shrink-0 rounded-lg border border-border p-2 text-muted hover:text-red-600"
                    aria-label={t("common.delete")}
                    onclick={() => removeStep(index)}
                  >
                    <Trash2 size={14} />
                  </button>
                </li>
              {/each}
            </ul>
            <button
              type="button"
              class="mt-2 flex items-center gap-1 text-sm font-medium text-brand"
              onclick={addStep}
            >
              <Plus size={14} />
              {t("tasks.dictate.add_step")}
            </button>
          </div>

          {#if draft.links.length > 0}
            <div>
              <span class="mb-1 block text-sm font-medium text-text">{t("tasks.links.title")}</span>
              <ul class="space-y-1.5">
                {#each draft.links as link, index (index)}
                  <li class="flex items-center gap-2">
                    <input
                      bind:value={link.url}
                      class={inputClass}
                      aria-label={t("tasks.links.title")}
                    />
                    <button
                      type="button"
                      class="shrink-0 rounded-lg border border-border p-2 text-muted hover:text-red-600"
                      aria-label={t("common.delete")}
                      onclick={() => removeLink(index)}
                    >
                      <X size={14} />
                    </button>
                  </li>
                {/each}
              </ul>
            </div>
          {/if}

          <div class="flex flex-wrap items-center gap-4">
            <label class="flex items-center gap-2 text-sm text-text">
              <input
                type="checkbox"
                checked={draft.requires_interaction === true}
                onchange={(e) => draft && (draft.requires_interaction = e.currentTarget.checked)}
                class="rounded border-border"
              />
              {t("tasks.field.requires_interaction")}
            </label>
            <label class="flex items-center gap-2 text-sm text-text">
              <input
                type="checkbox"
                checked={draft.visible_to_client === true}
                onchange={(e) => draft && (draft.visible_to_client = e.currentTarget.checked)}
                class="rounded border-border"
              />
              {t("tasks.field.visible_to_client")}
            </label>
          </div>

          <p class="text-xs text-muted">{t("tasks.dictate.nothing_saved_yet")}</p>

          <div class="flex justify-end gap-2 border-t border-border pt-3">
            <button
              type="button"
              class="rounded-lg border border-border px-4 py-2 text-sm"
              onclick={close}
            >
              {t("common.cancel")}
            </button>
            <Button loading={busy.active} disabled={!draft.title?.trim()}>
              {t("tasks.dictate.create")}
            </Button>
          </div>
        </form>
      {:else if !status}
        <p class="flex items-center gap-2 text-sm text-muted">
          <Mic size={14} />
          {t("tasks.dictate.hint")}
        </p>
      {/if}
    </div>
  {/if}
</SlideOver>
