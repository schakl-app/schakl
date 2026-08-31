<script lang="ts">
  /**
   * "schakl vult deze taak in" — the live state of an AI enrichment run (#327), as a progress
   * indicator rather than a sentence.
   *
   * The point of the whole feature is that **nobody waits**: approving an email queues the run
   * and returns immediately. But since the approve that *created* the task now lands the
   * reviewer on it in edit mode, somebody is very often looking straight at a task that is
   * about to change under them — and a pulsing icon over one line of text says "something is
   * happening" without saying how far along it is or whether it is still moving at all.
   *
   * So the run states are drawn as phases with a bar: queued (accepted, not started), running
   * (the worker has the message), and a short confirmation when it lands. The bar's width is
   * **not** a measured percentage — the endpoint answers one column and the job reports no
   * progress of its own — so it is `aria-hidden` decoration and the live region carries the
   * phase in words. A creeping bar that is honest about what it is beats a spinner: it shows
   * the run is still alive, which is the one thing a reader of a background job wants.
   *
   * Polling is deliberately small. It asks one endpoint that answers one column
   * (`/ai-status`) rather than re-fetching the card, and it stops the moment the run settles or
   * the ceiling is reached — a tab left open on a task must not poll the API all afternoon
   * (docs/PERFORMANCE.md). The single expensive call, `invalidateAll()`, happens exactly once.
   *
   * **In edit mode it is never automatic.** `invalidateAll()` re-runs the load, and this page's
   * edit fields render `value={task.description}`, so a reload while somebody is typing writes
   * the server's answer over their words. Landing in edit mode is now the common case, so the
   * finish offers a button instead: the reader decides when the page is redrawn.
   *
   * Terminal states are shown rather than swallowed. A run that failed or found nothing says so
   * quietly, because a control that cannot fail visibly is worse than one that refuses
   * (CLAUDE.md §10).
   */
  import { Check, Sparkles, TriangleAlert } from "@lucide/svelte";

  import { invalidateAll } from "$app/navigation";
  import { t } from "$lib/core/i18n";

  let {
    taskId,
    status = null,
    editing = false,
  }: {
    taskId: string;
    /** The task's stored `ai_status`; `null` on a task no AI run ever touched. */
    status?: string | null;
    /** The page is in edit mode — see the note above: the redraw becomes a button. */
    editing?: boolean;
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

  /**
   * How many polls this run has survived, which is all the progress there is to report: the
   * endpoint answers one column and the job publishes no percentage of its own. Drives the
   * bar's creep, never a number on screen.
   */
  let ticks = $state(0);
  /** The run finished while this page was open — the only case where anything is announced. */
  let landed = $state(false);
  /** Set when the reader has asked for the redraw, so the strip stops offering it. */
  let revealed = $state(false);

  const width = $derived.by(() => {
    if (landed) return 100;
    if (live === "queued") return 12;
    if (live === "running") return Math.min(88, 30 + ticks * 4);
    return 0;
  });

  const phaseKey = $derived(live === "queued" ? "tasks.ai.queued" : "tasks.ai.enriching");

  /**
   * Draw what the run wrote.
   *
   * Two mechanisms, because `invalidateAll()` is not enough in edit mode: the description is a
   * *mounted* rich-text editor, not a controlled input, so it keeps the value it was created
   * with however many times the load re-runs — the button would say "toon wat is aangevuld" and
   * visibly do nothing, which is the one thing worse than not offering it. A reload is honest
   * there, and it is an explicit press on an explicit notice rather than something that happens
   * to somebody mid-sentence. In use mode nothing is mounted over the data, so the cheap call
   * is also the correct one.
   */
  async function reveal() {
    revealed = true;
    if (editing) location.reload();
    else await invalidateAll();
  }

  $effect(() => {
    if (!running) return;
    let polls = 0;
    let stopped = false;
    const timer = setInterval(async () => {
      if (stopped || ++polls > MAX_POLLS) {
        clearInterval(timer);
        return;
      }
      ticks = polls;
      try {
        const response = await fetch(`/api/v1/tasks/${taskId}/ai-status`, {
          headers: { accept: "application/json" },
        });
        if (!response.ok) return;
        const next = ((await response.json()) as { ai_status: string | null }).ai_status;
        if (next === live) return;
        live = next;
        if (next === "done") {
          clearInterval(timer);
          landed = true;
          // The one heavy call, and only when there is something new to draw — and only when
          // there is no half-typed form for it to overwrite.
          if (!editing) await reveal();
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
    class="rounded-xl border border-brand/30 bg-brand/5 px-4 py-3 text-sm text-text"
    role="status"
    aria-live="polite"
  >
    <div class="flex items-center gap-2">
      <Sparkles size={16} class="shrink-0 animate-pulse text-brand" />
      <span>{t(phaseKey)}</span>
    </div>
    <!-- Decoration: the phase above is the answer a screen reader gets, because this width is
         a sign of life and not a measurement. -->
    <div class="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-brand/15" aria-hidden="true">
      <div
        class="h-full rounded-full bg-brand transition-[width] duration-1000 ease-out"
        style="width: {width}%"
      ></div>
    </div>
  </div>
{:else if landed && !revealed}
  <!-- Finished while somebody was editing: the fields it wrote are on the server and the form
       on screen is the reader's, so the redraw is theirs to ask for. -->
  <div
    class="flex flex-wrap items-center gap-2 rounded-xl border border-brand/30 bg-brand/5 px-4 py-2.5 text-sm text-text"
    role="status"
    aria-live="polite"
  >
    <Check size={16} class="shrink-0 text-brand" />
    <span>{t("tasks.ai.done")}</span>
    <button type="button" class="font-medium text-brand hover:underline" onclick={reveal}>
      {t("tasks.ai.show")}
    </button>
  </div>
{:else if live === "failed" || live === "skipped"}
  <div
    class="flex items-center gap-2 rounded-xl border border-border bg-surface-raised px-4 py-2.5 text-sm text-text-muted"
  >
    <TriangleAlert size={16} class="shrink-0" />
    <span>{t(live === "failed" ? "tasks.ai.failed" : "tasks.ai.skipped")}</span>
  </div>
{/if}
