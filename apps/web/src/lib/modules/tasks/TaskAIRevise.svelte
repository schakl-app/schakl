<script lang="ts">
  /**
   * Change a task in words — the box under the notes on the card and in the review slide-over.
   *
   * "voeg een stap toe voor de DNS, deadline vrijdag, zet erbij dat de klant het in het blauw
   * wil": one instruction, one press, and the task is changed **as the person who typed it**
   * (`POST /tasks/{id}/ai/revise`, `tasks/assist.py`) — every edit on the trail under their
   * name and behind every rule an ordinary edit meets. The answer is a diff: what the
   * instruction did not mention is left exactly as it was.
   *
   * The host decides what happens afterwards through `onapplied`: the card reloads its data,
   * the slide-over adopts the row the API hands back. Both are told the model's one-sentence
   * summary and the kinds of change that landed, so the reader knows what to check without
   * re-reading the whole task.
   *
   * Off means invisible (#126): the host draws this only when `aiEnabled(user, "task_assist")`
   * and the viewer may edit the task, so the box never answers a 409 on the press that matters.
   * A budget refusal is the one refusal that gets its own sentence — it is a decision the org
   * made, not a fault.
   */
  import { Sparkles } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";
  import Button from "$lib/core/ui/Button.svelte";

  export interface ReviseResult {
    task: Record<string, unknown>;
    summary: string | null;
    changed: string[];
    truncated: boolean;
  }

  let {
    taskId,
    onapplied,
    before,
    compact = false,
    id = "task-ai-revise",
  }: {
    taskId: string;
    /** The task as it now stands, and what changed; the host redraws from it. */
    onapplied?: (result: ReviseResult) => void | Promise<void>;
    /** Ran first; `false` stops the press — a host with unsaved fields saves them, so the
     *  model reads what the reader sees. */
    before?: () => boolean | Promise<boolean>;
    /** A tighter layout for a slide-over. */
    compact?: boolean;
    id?: string;
  } = $props();

  let instruction = $state("");
  let busy = $state(false);
  let error = $state<string | null>(null);
  let budgetReached = $state(false);
  let summary = $state<string | null>(null);
  let truncated = $state(false);
  let nothingChanged = $state(false);

  async function apply(override = false) {
    const text = instruction.trim();
    if (!text || busy) return;
    if (before && !(await before())) return;
    busy = true;
    error = null;
    summary = null;
    nothingChanged = false;
    truncated = false;
    try {
      const res = await fetch(`/api/v1/tasks/${taskId}/ai/revise`, {
        method: "POST",
        headers: { "content-type": "application/json", accept: "application/json" },
        body: JSON.stringify({ instruction: text, override_budget: override }),
      });
      if (!res.ok) {
        const payload = await res.json().catch(() => null);
        if (payload?.error?.code === "ai_budget_reached") budgetReached = true;
        else error = payload?.error?.message ?? "errors.ai_provider_error";
        return;
      }
      budgetReached = false;
      const result = (await res.json()) as ReviseResult;
      summary = result.summary;
      truncated = result.truncated;
      nothingChanged = result.changed.length === 0;
      if (result.changed.length > 0) instruction = "";
      await onapplied?.(result);
    } catch {
      error = "errors.ai_provider_error";
    } finally {
      busy = false;
    }
  }

  function onkeydown(event: KeyboardEvent) {
    // Enter applies, Shift+Enter breaks a line — the composer convention the assistant set.
    if (event.key === "Enter" && !event.shiftKey && !busy) {
      event.preventDefault();
      void apply();
    }
  }
</script>

<div class={compact ? "space-y-2" : "space-y-2 rounded-xl border border-dashed border-border p-3"}>
  <label for={id} class="flex items-center gap-1.5 text-sm font-medium text-text">
    <Sparkles size={14} class="text-brand" aria-hidden="true" />
    {t("tasks.ai.revise_label")}
  </label>
  <div class="flex items-start gap-2">
    <textarea
      {id}
      bind:value={instruction}
      rows={compact ? 2 : 2}
      disabled={busy}
      placeholder={t("tasks.ai.revise_placeholder")}
      {onkeydown}
      class="min-w-0 flex-1 rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand disabled:opacity-60"
    ></textarea>
    <Button
      type="button"
      size="sm"
      variant="secondary"
      loading={busy}
      disabled={!instruction.trim()}
      onclick={() => apply()}
    >
      {t("tasks.ai.revise_submit")}
    </Button>
  </div>
  {#if busy}
    <p class="text-xs text-text-muted" aria-live="polite">{t("tasks.ai.revise_busy")}</p>
  {:else if budgetReached}
    <p class="text-xs text-amber-700 dark:text-amber-400" role="alert">
      {t("ai.budget_notice")}
      <button type="button" class="ml-1 underline" onclick={() => apply(true)}>
        {t("ai.budget_proceed")}
      </button>
    </p>
  {:else if error}
    <p class="text-xs text-red-600 dark:text-red-400" role="alert">{t(error)}</p>
  {:else if nothingChanged}
    <p class="text-xs text-text-muted" aria-live="polite">{t("tasks.ai.revise_nothing")}</p>
  {:else if summary}
    <p class="text-xs text-text-muted" aria-live="polite">
      <Sparkles size={12} class="mr-1 inline text-brand" aria-hidden="true" />{summary}
      {#if truncated}
        <span class="text-amber-700 dark:text-amber-400">{t("tasks.ai.revise_truncated")}</span>
      {/if}
    </p>
  {/if}
</div>
