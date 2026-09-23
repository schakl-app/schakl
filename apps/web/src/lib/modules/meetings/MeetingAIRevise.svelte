<script lang="ts">
  /**
   * Change a meeting in words — the box on the meeting page (`TaskAIRevise`'s shape).
   *
   * "zet de klant op Nova, S2 is Jan, voeg een actiepunt toe voor Sanne: logo sturen, deadline
   * vrijdag": one instruction, one press, and the meeting is changed **as the person who typed
   * it** (`POST /meetings/{id}/ai/revise`, `meetings/assist.py`) — the definition fields, the
   * roster and, while the minutes are under review, every line of the draft, each through the
   * service an ordinary edit goes through. The answer is a diff: what the instruction did not
   * mention stays.
   *
   * The instruction may be spoken as well as typed; the microphone lands the words **in the
   * field**, never straight into the model (#246), through the meeting's own transcribe route.
   * The host says what happens afterwards through `onapplied` (it re-reads the row) and may
   * save its own unsaved edits first through `before`, so the model reads what the reader sees.
   */
  import Sparkles from "@lucide/svelte/icons/sparkles";
  import { onMount } from "svelte";

  import { page } from "$app/state";
  import { aiEnabled } from "$lib/core/ai";
  import { t } from "$lib/core/i18n";
  import Button from "$lib/core/ui/Button.svelte";
  import {
    MAX_TASK_RECORD_MS,
    Recorder,
    VoiceButton,
    recordingSupported,
    transcribeClip,
  } from "$lib/core/voice";

  export interface MeetingReviseResult {
    meeting: Record<string, unknown>;
    summary: string | null;
    changed: string[];
    truncated: boolean;
  }

  let {
    meetingId,
    onapplied,
    before,
    id = "meeting-ai-revise",
  }: {
    meetingId: string;
    onapplied?: (result: MeetingReviseResult) => void | Promise<void>;
    before?: () => boolean | Promise<boolean>;
    id?: string;
  } = $props();

  let instruction = $state("");
  let busy = $state(false);
  let error = $state<string | null>(null);
  let budgetReached = $state(false);
  let summary = $state<string | null>(null);
  let truncated = $state(false);
  let nothingChanged = $state(false);
  let field = $state<HTMLTextAreaElement | null>(null);

  const recorder = new Recorder(MAX_TASK_RECORD_MS);
  let micSupported = $state(false);
  let voiceStatus = $state<string | null>(null);
  let limitNote = $state<string | null>(null);
  onMount(() => {
    micSupported = recordingSupported();
    return () => recorder.abort();
  });
  const canDictate = $derived(aiEnabled(page.data.user, "speech") && micSupported);

  async function dictate() {
    error = null;
    limitNote = null;
    const audio = await recorder.start();
    if (recorder.error) {
      error = recorder.error;
      return;
    }
    if (!audio) return;
    limitNote = recorder.stoppedAtLimit
      ? t("voice.limit_reached", { minutes: Math.round(recorder.maxMs / 60_000) })
      : null;
    voiceStatus = "voice.transcribing";
    try {
      const outcome = await transcribeClip(
        `/api/v1/meetings/${meetingId}/ai/transcribe`,
        audio,
        page.data.locale ?? "nl",
      );
      if (outcome.budget) {
        budgetReached = true;
        return;
      }
      if (outcome.error || !outcome.text) {
        error = outcome.error ?? "voice.error_no_speech";
        return;
      }
      instruction = instruction.trim() ? `${instruction.trim()} ${outcome.text}` : outcome.text;
      field?.focus();
    } finally {
      voiceStatus = null;
    }
  }

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
      const res = await fetch(`/api/v1/meetings/${meetingId}/ai/revise`, {
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
      const result = (await res.json()) as MeetingReviseResult;
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
    if (event.key === "Enter" && !event.shiftKey && !busy) {
      event.preventDefault();
      void apply();
    }
  }
</script>

<!-- A card of its own, in the brand's tint: the one control on the desk that changes any part
     of it in words. The field takes the width, the microphone sits inside it, and the buttons
     go under it — a textarea squeezed between two buttons read as an afterthought. -->
<section
  class="rounded-xl border border-brand/30 bg-brand/5 p-4 shadow-sm"
  aria-labelledby={`${id}-title`}
>
  <h2 id={`${id}-title`} class="flex items-center gap-2 text-sm font-semibold text-text">
    <span
      class="inline-flex size-6 items-center justify-center rounded-full bg-brand/15 text-brand"
    >
      <Sparkles size={13} aria-hidden="true" />
    </span>
    {t("meetings.ai.revise_title")}
  </h2>
  <label for={id} class="sr-only">{t("meetings.ai.revise_label")}</label>
  <div class="relative mt-3">
    <textarea
      {id}
      bind:this={field}
      bind:value={instruction}
      rows="3"
      disabled={busy}
      placeholder={t("meetings.ai.revise_placeholder")}
      {onkeydown}
      class="w-full resize-y rounded-lg border border-border bg-surface-raised px-3 py-2 pr-11 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand disabled:opacity-60"
    ></textarea>
    {#if canDictate}
      <div class="absolute top-1.5 right-1.5">
        <VoiceButton
          {recorder}
          onstart={() => void dictate()}
          onstop={() => recorder.stop()}
          disabled={busy || voiceStatus !== null}
        />
      </div>
    {/if}
  </div>
  <div class="mt-2 flex flex-wrap items-center justify-between gap-2">
    <p class="text-xs text-text-muted">{t("meetings.ai.revise_hint")}</p>
    <Button
      type="button"
      size="sm"
      loading={busy}
      disabled={!instruction.trim()}
      onclick={() => apply()}
    >
      <Sparkles size={13} />
      {t("meetings.ai.revise_submit")}
    </Button>
  </div>
  {#if busy}
    <p class="mt-2 text-xs text-text-muted" aria-live="polite">{t("meetings.ai.revise_busy")}</p>
  {:else if voiceStatus}
    <p class="mt-2 text-xs text-text-muted" aria-live="polite">{t(voiceStatus)}</p>
  {:else if budgetReached}
    <p class="mt-2 text-xs text-amber-700 dark:text-amber-400" role="alert">
      {t("ai.budget_notice")}
      <button type="button" class="ml-1 underline" onclick={() => apply(true)}>
        {t("ai.budget_proceed")}
      </button>
    </p>
  {:else if error}
    <p class="mt-2 text-xs text-red-600 dark:text-red-400" role="alert">{t(error)}</p>
  {:else if limitNote}
    <p class="mt-2 text-xs text-text-muted" role="status">{limitNote}</p>
  {:else if nothingChanged}
    <p class="mt-2 text-xs text-text-muted" aria-live="polite">{t("meetings.ai.revise_nothing")}</p>
  {:else if summary}
    <p class="mt-2 rounded-lg bg-surface-raised px-3 py-2 text-xs text-text" aria-live="polite">
      <Sparkles size={12} class="mr-1 inline text-brand" aria-hidden="true" />{summary}
      {#if truncated}
        <span class="text-amber-700 dark:text-amber-400">{t("meetings.ai.revise_truncated")}</span>
      {/if}
    </p>
  {/if}
</section>
