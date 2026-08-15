<script lang="ts">
  /**
   * "schakl leest de e-mail" — the live state of an AI enrichment run (#327).
   *
   * The point of the whole feature is that **nobody waits**: approving an email queues the run
   * and returns immediately, so this is what the task card says while a worker reads the
   * message. It matters most for the user who approves and then opens the task straight away;
   * anyone arriving a minute later simply finds the task already filled in.
   *
   * Polling is deliberately small. It asks one endpoint that answers one column
   * (`/ai-status`) rather than re-fetching the card, and it stops the moment the run settles or
   * the ceiling is reached — a tab left open on a task must not poll the API all afternoon
   * (docs/PERFORMANCE.md). The single expensive call, `invalidateAll()`, happens exactly once:
   * when the run finished and there is actually new content to render.
   *
   * Terminal states are shown rather than swallowed. `done` says nothing — the notes, checklist
   * and links are on screen and the activity trail records who wrote them — but a run that
   * failed or found nothing says so quietly, because a control that cannot fail visibly is
   * worse than one that refuses (CLAUDE.md §10).
   */
  import { Sparkles, TriangleAlert } from "@lucide/svelte";

  import { invalidateAll } from "$app/navigation";
  import { t } from "$lib/core/i18n";

  let {
    taskId,
    status = null,
  }: {
    taskId: string;
    /** The task's stored `ai_status`; `null` on a task no AI run ever touched. */
    status?: string | null;
  } = $props();

  /** How often to ask, and how long to keep asking. Four seconds × 45 ≈ three minutes, which
   *  comfortably outlasts the job's own retry ladder without becoming a background tab that
   *  polls for ever. */
  const POLL_MS = 4000;
  const MAX_POLLS = 45;

  let live = $state<string | null>(status);
  // The prop is the server's answer on the last render; it wins whenever the page reloads.
  $effect(() => {
    live = status;
  });

  const running = $derived(live === "queued" || live === "running");

  $effect(() => {
    if (!running) return;
    let polls = 0;
    let stopped = false;
    const timer = setInterval(async () => {
      if (stopped || ++polls > MAX_POLLS) {
        clearInterval(timer);
        return;
      }
      try {
        const response = await fetch(`/api/v1/tasks/${taskId}/ai-status`, {
          headers: { accept: "application/json" },
        });
        if (!response.ok) return;
        const next = ((await response.json()) as { ai_status: string | null }).ai_status;
        if (next === live) return;
        live = next;
        if (next === "done") {
          // The one heavy call, and only when there is something new to draw.
          clearInterval(timer);
          await invalidateAll();
        }
      } catch {
        // A dropped poll is not a failed run: the next tick asks again, and the ceiling ends it.
      }
    }, POLL_MS);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  });
</script>

{#if running}
  <div
    class="flex items-center gap-2 rounded-xl border border-brand/30 bg-brand/5 px-4 py-2.5 text-sm text-text"
    role="status"
    aria-live="polite"
  >
    <Sparkles size={16} class="shrink-0 animate-pulse text-brand" />
    <span>{t("tasks.ai.enriching")}</span>
  </div>
{:else if live === "failed" || live === "skipped"}
  <div
    class="flex items-center gap-2 rounded-xl border border-border bg-surface-raised px-4 py-2.5 text-sm text-text-muted"
  >
    <TriangleAlert size={16} class="shrink-0" />
    <span>{t(live === "failed" ? "tasks.ai.failed" : "tasks.ai.skipped")}</span>
  </div>
{/if}
