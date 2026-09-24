<script lang="ts">
  /**
   * A task from one action item, filled in by schakl and checked by a person before it exists.
   *
   * The e-mail approve's "laat schakl deze taak invullen" (#327) on the review desk, in the
   * dictation sheet's shape (#382): the reviewer presses *Taak maken met schakl* beside an
   * action item, the sheet asks the API for a draft (`POST …/action-items/draft-task` — the
   * model reads the words around the item, so the steps the meeting enumerated and the deadline
   * that was spoken land in the form), marks every field it filled with a ✦, lets the reviewer
   * correct all of it, and only *Aanmaken* writes anything — one call, steps and links included
   * (`?/createItemTask`). The item then carries its task and the contact moment lists it —
   * the only way an action item becomes a task.
   *
   * Two rules from the surfaces this borrows from. **A draft that fails still opens the form**,
   * filled from the minutes themselves — losing the click to a provider hiccup is the worse
   * outcome, and the sheet says which of the two happened. And **the client is the meeting's**:
   * the picker is not drawn, because a task made from a meeting with Nova is Nova's.
   */
  import Plus from "@lucide/svelte/icons/plus";
  import Sparkles from "@lucide/svelte/icons/sparkles";
  import Trash2 from "@lucide/svelte/icons/trash-2";
  import X from "@lucide/svelte/icons/x";
  import { untrack } from "svelte";

  import { enhance } from "$app/forms";
  import { fmtDayMonth } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { memberArchivedLabel, splitMemberOptions } from "$lib/core/members";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import DurationInput from "$lib/core/ui/DurationInput.svelte";
  import SlideOver from "$lib/core/ui/SlideOver.svelte";
  import { projectArchivedLabel, splitProjectOptions } from "$lib/modules/projects/picker";

  import { fmtClock } from "./format";
  import type { MeetingParticipant, MinutesActionItem } from "./types";

  interface Member {
    user_id: string;
    full_name: string | null;
    email: string | null;
    is_active?: boolean;
  }
  interface Step {
    title: string;
    description?: string | null;
  }
  interface Link {
    url: string;
    title?: string | null;
  }
  interface Draft {
    title: string;
    description: string;
    due_date: string;
    priority: string;
    project_id: string;
    /** `u:<id>` a colleague, `c:<id>` a contact of the client, `` nobody. */
    owner: string;
    allocated_minutes: number | null;
    checklist_title: string;
    checklist_items: Step[];
    links: Link[];
    requires_interaction: boolean;
    visible_to_client: boolean;
    truncated: boolean;
  }

  let {
    open = $bindable(false),
    meetingId,
    meetingTitle,
    companyId,
    companyName = null,
    projectId = null,
    index,
    item,
    participants = [],
    members = [],
    projects = [],
    aiAvailable = true,
    before,
    onsaved,
  }: {
    open?: boolean;
    meetingId: string;
    meetingTitle: string;
    companyId: string | null;
    companyName?: string | null;
    projectId?: string | null;
    /** Which action item of the *stored* minutes; the host saves its draft first (`before`). */
    index: number;
    item: MinutesActionItem;
    participants?: MeetingParticipant[];
    members?: Member[];
    projects?: { id: string; name: string; status?: string; company_id?: string | null }[];
    /** Whether the draft may be asked for at all (the AI feature is on and the org can). */
    aiAvailable?: boolean;
    before?: () => boolean | Promise<boolean>;
    onsaved?: (taskId: string) => void | Promise<void>;
  } = $props();

  const busy = new InFlight();
  let draft = $state<Draft | null>(null);
  let drafting = $state(false);
  let draftFailed = $state(false);
  let budgetReached = $state(false);
  let filled = $state<string[]>([]);
  let error = $state<string | null>(null);

  const memberPicker = $derived(splitMemberOptions(members));
  const projectPicker = $derived(
    splitProjectOptions(projects, {
      selectedId: draft?.project_id ?? "",
      companyId: companyId ?? "",
    }),
  );
  /** Who may hold it: every colleague, plus the client's people at the table (#273). */
  const ownerItems = $derived.by(() => {
    const out: { value: string; label: string; hint?: string }[] = [];
    for (const p of participants) {
      if (p.contact_id)
        out.push({ value: `c:${p.contact_id}`, label: p.name, hint: t("party.contact") });
    }
    for (const m of memberPicker.live) out.push({ value: `u:${m.value}`, label: m.label });
    return out;
  });
  const ownerArchived = $derived(
    memberPicker.retired.map((m) => ({ value: `u:${m.value}`, label: m.label })),
  );

  function marked(key: string): boolean {
    return filled.includes(key);
  }

  function fromItem(): Draft {
    return {
      title: item.title,
      description: item.description ?? "",
      due_date: item.due_date ?? "",
      priority: "normal",
      project_id: projectId ?? "",
      owner: item.owner_contact_id
        ? `c:${item.owner_contact_id}`
        : item.assignee_user_id
          ? `u:${item.assignee_user_id}`
          : "",
      allocated_minutes: null,
      checklist_title: "",
      checklist_items: [],
      links: [],
      requires_interaction: false,
      visible_to_client: false,
      truncated: false,
    };
  }

  async function askSchakl(override = false) {
    drafting = true;
    draftFailed = false;
    budgetReached = false;
    error = null;
    try {
      const res = await fetch(`/api/v1/meetings/${meetingId}/action-items/draft-task`, {
        method: "POST",
        headers: { "content-type": "application/json", accept: "application/json" },
        body: JSON.stringify({ index, override_budget: override }),
      });
      if (!res.ok) {
        const payload = await res.json().catch(() => null);
        if (payload?.error?.code === "ai_budget_reached") budgetReached = true;
        else draftFailed = true;
        return;
      }
      const answered = await res.json();
      const marks: string[] = [];
      for (const key of [
        "title",
        "description",
        "due_date",
        "priority",
        "project_id",
        "assignee_user_id",
        "allocated_minutes",
      ] as const) {
        if (answered[key] !== null && answered[key] !== undefined) marks.push(key);
      }
      if (answered.checklist_items?.length) marks.push("checklist");
      if (answered.links?.length) marks.push("links");
      filled = marks;
      const base = fromItem();
      draft = {
        ...base,
        title: answered.title || base.title,
        description: answered.description ?? base.description,
        due_date: answered.due_date ?? base.due_date,
        priority: answered.priority ?? "normal",
        project_id: answered.project_id ?? base.project_id,
        owner: item.owner_contact_id
          ? base.owner
          : answered.assignee_user_id
            ? `u:${answered.assignee_user_id}`
            : base.owner,
        allocated_minutes: answered.allocated_minutes ?? null,
        checklist_title: answered.checklist_title ?? "",
        checklist_items: answered.checklist_items ?? [],
        links: answered.links ?? [],
        requires_interaction: answered.requires_interaction === true,
        visible_to_client: answered.visible_to_client === true,
        truncated: answered.truncated === true,
      };
    } catch {
      draftFailed = true;
    } finally {
      drafting = false;
      if (!draft) draft = fromItem();
    }
  }

  // Opening asks schakl once; the host saved its unsaved draft first (`before`), so the item
  // the API reads is the item on the screen. `asked` is what makes it once: the effect reads
  // `open` and nothing else on purpose, and a host re-render must not ask (and re-save) again.
  let asked = $state(false);
  $effect(() => {
    if (!open) {
      draft = null;
      filled = [];
      error = null;
      draftFailed = false;
      asked = false;
      return;
    }
    if (untrack(() => asked)) return;
    asked = true;
    void (async () => {
      if (before && !(await before())) {
        error = "errors.validation";
        draft = fromItem();
        return;
      }
      if (aiAvailable) await askSchakl();
      else draft = fromItem();
    })();
  });

  const summary = $derived.by(() => {
    if (!draft) return "";
    const parts: string[] = [];
    if (draft.due_date) parts.push(fmtDayMonth(draft.due_date));
    if (companyName) parts.push(companyName);
    const project = projects.find((p) => p.id === draft?.project_id);
    if (project?.name) parts.push(project.name);
    const owner = ownerItems.find((o) => o.value === draft?.owner);
    if (owner) parts.push(owner.label);
    if (draft.checklist_items.length)
      parts.push(
        draft.checklist_items.length === 1
          ? t("tasks.dictate.steps_count_one")
          : t("tasks.dictate.steps_count", { count: draft.checklist_items.length }),
      );
    return parts.filter(Boolean).join(" · ");
  });

  const payload = $derived(
    draft
      ? JSON.stringify({
          index,
          title: draft.title.trim(),
          description: draft.description.trim() || null,
          due_date: draft.due_date,
          priority: draft.priority || "normal",
          project_id: draft.project_id || null,
          assignee_user_id: draft.owner.startsWith("u:") ? draft.owner.slice(2) : null,
          assignee_contact_id: draft.owner.startsWith("c:") ? draft.owner.slice(2) : null,
          allocated_minutes: draft.allocated_minutes,
          checklist_title: draft.checklist_title.trim() || null,
          checklist_items: draft.checklist_items.filter((s) => s.title.trim()),
          links: draft.links.filter((l) => l.url.trim()),
          requires_interaction: draft.requires_interaction,
          visible_to_client: draft.visible_to_client,
        })
      : "",
  );
  const canCreate = $derived(
    !!draft && !!draft.title.trim() && !!draft.due_date && !!companyId && !!draft.owner,
  );

  function addStep() {
    if (draft) draft.checklist_items = [...draft.checklist_items, { title: "" }];
  }
  function removeStep(i: number) {
    if (draft) draft.checklist_items = draft.checklist_items.filter((_, n) => n !== i);
  }
  function removeLink(i: number) {
    if (draft) draft.links = draft.links.filter((_, n) => n !== i);
  }

  const inputClass =
    "w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<SlideOver bind:open title={t("meetings.task.title")} size="2xl">
  <div class="space-y-5 p-4">
    <!-- Where it came from: the item and the words it rests on stay in view beside the form. -->
    <div class="rounded-xl border border-border bg-surface px-4 py-3 text-sm">
      <p class="text-xs font-medium tracking-wide text-text-muted uppercase">
        {t("meetings.task.origin")}
      </p>
      <p class="mt-0.5 font-medium text-text">{meetingTitle}</p>
      {#if item.quote}
        <p class="mt-1 text-xs text-text-muted">
          <span class="font-medium">{t("meetings.task.quote")}:</span>
          {#if item.at != null}<span class="tabular-nums">{fmtClock(item.at)}</span> ·{/if}
          <span class="italic">“{item.quote}”</span>
        </p>
      {/if}
    </div>

    {#if drafting}
      <p class="flex items-center gap-2 text-sm text-text-muted" aria-live="polite">
        <Sparkles size={14} class="text-brand" />
        {t("meetings.task.drafting")}
      </p>
    {:else if draft}
      {#if draftFailed}
        <p class="text-sm text-amber-700 dark:text-amber-400" role="status">
          {t("meetings.task.draft_failed")}
        </p>
      {:else if budgetReached}
        <div
          class="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm dark:bg-amber-950/30"
        >
          <p class="text-text">{t("ai.budget_notice")}</p>
          <button
            type="button"
            class="mt-2 text-sm font-medium text-brand underline"
            onclick={() => askSchakl(true)}>{t("ai.budget_proceed")}</button
          >
        </div>
      {:else}
        <section class="rounded-lg border border-border bg-surface px-3 py-2.5">
          <p class="text-xs font-medium tracking-wide text-text-muted uppercase">
            {t("tasks.dictate.understood")}
          </p>
          <p class="mt-1 text-sm text-text">{summary || t("tasks.dictate.nothing_extra")}</p>
          {#if draft.truncated}
            <p class="mt-2 text-sm text-amber-700 dark:text-amber-400">
              {t("tasks.dictate.truncated")}
            </p>
          {/if}
        </section>
      {/if}
      <p class="text-xs text-text-muted">{t("meetings.task.hint")}</p>

      <form
        method="POST"
        action="?/createItemTask"
        class="space-y-4"
        use:enhance={busy.wrap("create", () => async ({ result, update }) => {
          if (result.type === "failure") {
            error = String(
              (result.data as { error?: string } | undefined)?.error ?? "errors.validation",
            );
            return;
          }
          // Starts something new: the sheet closes *first* and the host re-reads the row
          // itself (`onsaved`, forms:check). Closing before any invalidation matters: a
          // reload while the sheet is still open re-ran the opening effect, which saved the
          // host's stale draft over the item the API had just stamped with its task.
          const taskId =
            (result.type === "success"
              ? (result.data as { taskId?: string } | undefined)?.taskId
              : null) ?? null;
          open = false;
          // No reset: closing already drops the draft, and resetting bound inputs after the
          // form has unmounted writes into a draft that is gone.
          await update({ reset: false, invalidateAll: false });
          if (taskId) await onsaved?.(taskId);
        })}
      >
        <input type="hidden" name="payload" value={payload} />

        <div>
          <label
            for="item-task-title"
            class="mb-1 flex items-center gap-1 text-sm font-medium text-text"
          >
            {t("tasks.field.title")}
            {#if marked("title")}<Sparkles
                size={12}
                class="text-brand"
                aria-label={t("tasks.dictate.by_schakl")}
              />{/if}
          </label>
          <input id="item-task-title" bind:value={draft.title} required class={inputClass} />
        </div>

        <div>
          <label
            for="item-task-desc"
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
            id="item-task-desc"
            bind:value={draft.description}
            rows="4"
            class="{inputClass} resize-y"></textarea>
        </div>

        <div class="grid gap-3 sm:grid-cols-2">
          <div>
            <label
              for="item-task-due"
              class="mb-1 flex items-center gap-1 text-sm font-medium text-text"
            >
              {t("tasks.field.due_date")}
              {#if marked("due_date")}<Sparkles
                  size={12}
                  class="text-brand"
                  aria-label={t("meetings.task.due_spoken")}
                />{/if}
            </label>
            <DateInput
              id="item-task-due"
              name="item-task-due"
              value={draft.due_date}
              required
              onchange={(v) => draft && (draft.due_date = v)}
            />
            {#if marked("due_date")}
              <p class="mt-1 text-xs text-text-muted">{t("meetings.task.due_spoken")}</p>
            {/if}
          </div>
          <div>
            <span class="mb-1 flex items-center gap-1 text-sm font-medium text-text">
              {t("meetings.task.owner")}
              {#if marked("assignee_user_id")}<Sparkles size={12} class="text-brand" />{/if}
            </span>
            <Combobox
              id="item-task-owner"
              name="item-task-owner"
              items={ownerItems}
              archived={ownerArchived}
              archivedLabel={memberArchivedLabel()}
              value={draft.owner}
              allowEmpty={false}
              placeholder={t("tasks.assignees.add")}
              onselect={(v) => draft && (draft.owner = v)}
            />
            {#if !draft.owner}
              <p class="mt-1 text-xs text-text-muted">{t("errors.tasks_assignee_required")}</p>
            {:else if draft.owner.startsWith("c:")}
              <p class="mt-1 text-xs text-text-muted">
                {t("meetings.task.owner_contact", {
                  name: ownerItems.find((o) => o.value === draft?.owner)?.label ?? "",
                })}
              </p>
            {/if}
          </div>
          <div>
            <span class="mb-1 flex items-center gap-1 text-sm font-medium text-text">
              {t("tasks.field.project")}
              {#if marked("project_id")}<Sparkles size={12} class="text-brand" />{/if}
            </span>
            <Combobox
              id="item-task-project"
              name="item-task-project"
              items={projectPicker.live}
              archived={projectPicker.retired}
              archivedLabel={projectArchivedLabel()}
              value={draft.project_id}
              placeholder={t("common.none")}
              onselect={(v) => draft && (draft.project_id = v)}
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
              id="item-task-priority"
              name="item-task-priority"
              items={(["low", "normal", "high"] as const).map((key) => ({
                value: key,
                label: t(`tasks.priority.${key}`),
              }))}
              value={draft.priority}
              allowEmpty={false}
              onselect={(v) => draft && (draft.priority = v || "normal")}
            />
          </div>
        </div>

        <div>
          <span class="mb-1 flex items-center gap-1 text-sm font-medium text-text">
            {t("tasks.dictate.checklist")}
            {#if marked("checklist")}<Sparkles size={12} class="text-brand" />{/if}
          </span>
          <input
            bind:value={draft.checklist_title}
            placeholder={t("tasks.dictate.checklist_title")}
            class="{inputClass} mb-2"
            aria-label={t("tasks.dictate.checklist_title")}
          />
          <ul class="space-y-1.5">
            {#each draft.checklist_items as step, i (i)}
              <li class="flex items-center gap-2">
                <input
                  bind:value={step.title}
                  class={inputClass}
                  aria-label={t("tasks.dictate.step_n", { n: i + 1 })}
                />
                <button
                  type="button"
                  class="shrink-0 rounded-lg border border-border p-2 text-text-muted hover:text-red-600"
                  aria-label={t("common.delete")}
                  onclick={() => removeStep(i)}><Trash2 size={14} /></button
                >
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
            <span class="mb-1 flex items-center gap-1 text-sm font-medium text-text">
              {t("tasks.links.title")}
              {#if marked("links")}<Sparkles size={12} class="text-brand" />{/if}
            </span>
            <ul class="space-y-1.5">
              {#each draft.links as link, i (i)}
                <li class="flex items-center gap-2">
                  <input
                    bind:value={link.url}
                    class={inputClass}
                    aria-label={t("tasks.links.title")}
                  />
                  <button
                    type="button"
                    class="shrink-0 rounded-lg border border-border p-2 text-text-muted hover:text-red-600"
                    aria-label={t("common.delete")}
                    onclick={() => removeLink(i)}><X size={14} /></button
                  >
                </li>
              {/each}
            </ul>
          </div>
        {/if}

        <div class="flex flex-wrap items-center gap-4">
          <label class="flex items-center gap-2 text-sm text-text">
            <input
              type="checkbox"
              bind:checked={draft.requires_interaction}
              class="rounded border-border"
            />
            {t("tasks.field.requires_interaction")}
          </label>
          <label class="flex items-center gap-2 text-sm text-text">
            <input
              type="checkbox"
              bind:checked={draft.visible_to_client}
              class="rounded border-border"
            />
            {t("tasks.field.visible_to_client")}
          </label>
        </div>

        {#if !companyId}
          <p class="text-sm text-amber-700 dark:text-amber-400">
            {t("errors.tasks_company_required")}
          </p>
        {/if}
        {#if error}
          <p class="text-sm text-red-600 dark:text-red-400" role="alert">{t(error)}</p>
        {/if}
        <p class="text-xs text-text-muted">{t("tasks.dictate.nothing_saved_yet")}</p>

        <div class="flex justify-end gap-2 border-t border-border pt-3">
          <button
            type="button"
            class="rounded-lg border border-border px-4 py-2 text-sm"
            onclick={() => (open = false)}
          >
            {t("common.cancel")}
          </button>
          <Button loading={busy.active} disabled={!canCreate}>
            {t("meetings.task.create")}
          </Button>
        </div>
      </form>
    {/if}
  </div>
</SlideOver>
