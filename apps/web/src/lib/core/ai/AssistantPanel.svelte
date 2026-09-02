<script lang="ts">
  /**
   * The contextual assistant (#127): ask about the tenant's own data, grounded and cited.
   * Lives in a SlideOver opened from the shell; inherits the page's entity as a removable
   * chip. Conversations are per-user and ephemeral (v1) — the transcript lives here.
   *
   * Since `core/ai/apitools.py` it can also read anything the user may read and write a stated
   * few things (a task, a comment, hours, the timer). The panel does not know the list: the
   * `tool` event names the operation, its method and module, and the status line says "reads
   * domains…" or "saves…" from that — a write is announced as one, never as a lookup.
   *
   * And it can be spoken to. The microphone is the same control the time quick-add and the
   * task sheet use (`core/voice`), and the words land in the composer rather than being sent:
   * docs/VOICE.md's rule that a transcript is read before it is acted on, kept here for a
   * message that may end in a write.
   */
  import { page } from "$app/state";
  import { CircleStop, SendHorizontal, Sparkles, X } from "@lucide/svelte";
  import { onMount } from "svelte";

  import { t } from "$lib/core/i18n";
  import Markdown from "$lib/core/ui/Markdown.svelte";
  import {
    MAX_CHAT_RECORD_MS,
    Recorder,
    VoiceButton,
    recordingSupported,
    transcribeClip,
  } from "$lib/core/voice";

  import { aiEnabled, sourceHref, type AISource, type AssistantEntity } from "./index";
  import { streamAI, type AIToolEvent } from "./stream";

  interface Turn {
    role: "user" | "assistant";
    content: string;
    sources?: AISource[];
  }

  let { context = null }: { context?: AssistantEntity | null } = $props();

  let turns = $state<Turn[]>([]);
  let input = $state("");
  let streaming = $state(false);
  let toolStatus = $state<string | null>(null);
  let error = $state<string | null>(null);
  let budgetBlocked = $state(false);
  let useContext = $state(true);
  let abort: AbortController | null = null;
  let scroller: HTMLDivElement | undefined = $state();

  const recorder = new Recorder(MAX_CHAT_RECORD_MS);
  let micSupported = $state(false);
  let voiceStatus = $state<string | null>(null);
  /** "The cap was reached; this much was kept" — said, because a recording that ends on its
   *  own and says nothing reads as broken. */
  let voiceNote = $state<string | null>(null);

  onMount(() => {
    micSupported = recordingSupported();
    // The SlideOver unmounts its content on close, so this is every exit at once.
    return () => recorder.abort();
  });

  // The org has a provider that can transcribe (`speech`, resolved server-side, and reported
  // only while a dictating feature — this one included — is on) and this browser can record.
  // No write permission: the words become a message the user still sends.
  const canDictate = $derived(aiEnabled(page.data.user, "speech") && micSupported);

  const activeContext = $derived(useContext && context ? context : null);
  const suggestions = $derived.by(() => {
    const kind = activeContext?.entity_type;
    if (kind === "company" || kind === "project" || kind === "task") {
      return [t(`ai.assistant.suggest_${kind}_1`), t(`ai.assistant.suggest_${kind}_2`)];
    }
    return [t("ai.assistant.suggest_generic_1"), t("ai.assistant.suggest_generic_2")];
  });

  function toolLabel(detail: AIToolEvent): string {
    if (detail.method && detail.method !== "GET") return t("ai.assistant.writing");
    const module = detail.module ?? detail.name.split(".")[0];
    const known = `ai.assistant.searching_${module}`;
    const label = t(known);
    if (label !== known) return label;
    if (detail.module) {
      // The module's own display name, as the modules screen prints it — the same fallback
      // `moduleLabel` uses, so an unknown module reads as its key rather than as nothing.
      const moduleKey = `module.${detail.module}.label`;
      const moduleName = t(moduleKey);
      if (moduleName !== moduleKey) return t("ai.assistant.reading", { module: moduleName });
    }
    return t("ai.assistant.searching");
  }

  async function send(text: string, overrideBudget = false) {
    const question = text.trim();
    if (!question || streaming) return;
    error = null;
    voiceNote = null;
    budgetBlocked = false;
    input = "";
    turns.push({ role: "user", content: question });
    const history = turns.map((m) => ({ role: m.role, content: m.content }));
    // Take the reply back OUT of the $state array: mutations must go through the reactive
    // proxy — appending to the raw object streams into the void (no re-render, ever).
    turns.push({ role: "assistant", content: "" });
    const reply = turns[turns.length - 1];
    streaming = true;
    abort = new AbortController();
    try {
      const failure = await streamAI(
        "assistant",
        {
          messages: history,
          context: activeContext
            ? {
                entity_type: activeContext.entity_type,
                entity_id: activeContext.entity_id,
                label: activeContext.label,
              }
            : null,
          override_budget: overrideBudget,
        },
        {
          onText: (delta) => {
            reply.content += delta;
            toolStatus = null;
            scrollDown();
          },
          onTool: (_name, detail) => {
            toolStatus = toolLabel(detail);
          },
          onSources: (sources) => {
            reply.sources = sources;
          },
          onError: (_code, message) => {
            error = message;
          },
        },
        abort.signal,
      );
      if (failure) {
        turns.pop(); // the empty placeholder
        if (failure.code === "ai_budget_reached") {
          budgetBlocked = true;
          turns.pop(); // the question goes back into the composer for the retry
          input = question;
        } else {
          error = failure.message;
        }
      }
    } catch (err) {
      if (!(err instanceof DOMException && err.name === "AbortError")) {
        error = "errors.ai_provider_error";
      }
    } finally {
      streaming = false;
      toolStatus = null;
      abort = null;
      if (turns.at(-1)?.role === "assistant" && !turns.at(-1)?.content) turns.pop();
      scrollDown();
    }
  }

  async function dictate() {
    error = null;
    voiceNote = null;
    const audio = await recorder.start();
    if (recorder.error) {
      error = recorder.error;
      return;
    }
    if (!audio) return; // aborted, or nothing captured
    if (recorder.stoppedAtLimit) {
      voiceNote = t("voice.limit_reached", { minutes: Math.round(recorder.maxMs / 60_000) });
    }
    voiceStatus = "voice.transcribing";
    try {
      const outcome = await transcribeClip(
        "/ai/assistant/transcribe",
        audio,
        page.data.locale ?? "nl",
      );
      if (outcome.budget) {
        error = "errors.ai_budget_reached";
        return;
      }
      if (outcome.error || !outcome.text) {
        error = outcome.error ?? "voice.error_no_speech";
        return;
      }
      // Into the composer, not straight to the model: a spoken instruction may end in a
      // write, and the words are only correctable while they are still visible. A second
      // breath appends rather than replaces.
      input = input.trim() ? `${input.trim()} ${outcome.text}` : outcome.text;
    } finally {
      voiceStatus = null;
    }
  }

  function stop() {
    abort?.abort();
  }

  function scrollDown() {
    requestAnimationFrame(() => scroller?.scrollTo({ top: scroller.scrollHeight }));
  }

  function onkeydown(event: KeyboardEvent) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void send(input);
    }
  }
</script>

<div class="flex h-full flex-col">
  <div bind:this={scroller} class="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
    {#if turns.length === 0}
      <div class="flex items-start gap-2 text-sm text-text-muted">
        <Sparkles size={16} class="mt-0.5 shrink-0" />
        <p>{t("ai.assistant.intro")}</p>
      </div>
      <div class="space-y-2">
        {#each suggestions as suggestion (suggestion)}
          <button
            type="button"
            class="block w-full rounded-lg border border-border px-3 py-2 text-left text-sm text-text hover:border-brand"
            onclick={() => void send(suggestion)}
          >
            {suggestion}
          </button>
        {/each}
      </div>
    {/if}

    {#each turns as turn, i (i)}
      {#if turn.role === "user"}
        <div class="ml-6 rounded-lg bg-surface px-3 py-2 text-sm text-text">{turn.content}</div>
      {:else if turn.content || turn.sources?.length}
        <div class="text-sm text-text">
          <Markdown value={turn.content} />
          {#if turn.sources?.length}
            <div class="mt-2 flex flex-wrap gap-1.5">
              {#each turn.sources as source (source.type + source.id)}
                {@const href = sourceHref(source)}
                {#if href}
                  <a
                    {href}
                    class="rounded-full border border-border px-2 py-0.5 text-xs text-text-muted hover:border-brand hover:text-brand"
                    >{source.label || t(`ai.source.${source.type}`)}</a
                  >
                {:else}
                  <span
                    class="rounded-full border border-border px-2 py-0.5 text-xs text-text-muted"
                    >{source.label || t(`ai.source.${source.type}`)}</span
                  >
                {/if}
              {/each}
            </div>
          {/if}
        </div>
      {/if}
    {/each}

    {#if toolStatus}
      <p class="text-xs italic text-text-muted">{toolStatus}</p>
    {/if}
    {#if voiceStatus}
      <p class="text-xs italic text-text-muted" role="status">{t(voiceStatus)}</p>
    {/if}
    {#if voiceNote}
      <p class="text-xs text-text-muted" role="status">{voiceNote}</p>
    {/if}
    {#if error}
      <p class="text-sm text-red-600 dark:text-red-400">{t(error)}</p>
    {/if}
    {#if budgetBlocked}
      <div
        class="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200"
      >
        {t("ai.budget_notice")}
        <button type="button" class="ml-1 underline" onclick={() => void send(input, true)}
          >{t("ai.budget_proceed")}</button
        >
      </div>
    {/if}
  </div>

  <div class="border-t border-border p-3">
    {#if activeContext}
      <div class="mb-2">
        <span
          class="inline-flex items-center gap-1 rounded-full border border-border bg-surface px-2 py-0.5 text-xs text-text-muted"
        >
          {activeContext.label ?? t(`ai.source.${activeContext.entity_type}`)}
          <button
            type="button"
            aria-label={t("common.delete")}
            class="hover:text-text"
            onclick={() => (useContext = false)}><X size={12} /></button
          >
        </span>
      </div>
    {/if}
    <div class="flex items-end gap-2">
      <textarea
        rows="2"
        bind:value={input}
        {onkeydown}
        placeholder={t("ai.assistant.placeholder")}
        class="min-w-0 flex-1 resize-none rounded-lg border border-border bg-transparent px-3 py-2 text-sm text-text outline-none focus:border-brand"
      ></textarea>
      {#if canDictate}
        <VoiceButton
          {recorder}
          onstart={() => void dictate()}
          onstop={() => recorder.stop()}
          disabled={streaming || voiceStatus !== null}
        />
      {/if}
      {#if streaming}
        <button
          type="button"
          class="rounded-lg border border-border p-2 text-text-muted hover:border-brand hover:text-brand"
          aria-label={t("ai.assistant.stop")}
          title={t("ai.assistant.stop")}
          onclick={stop}><CircleStop size={18} /></button
        >
      {:else}
        <button
          type="button"
          class="rounded-lg bg-brand p-2 text-white hover:opacity-90 disabled:opacity-40"
          aria-label={t("ai.assistant.send")}
          title={t("ai.assistant.send")}
          disabled={!input.trim()}
          onclick={() => void send(input)}><SendHorizontal size={18} /></button
        >
      {/if}
    </div>
    <p class="mt-1.5 text-[11px] text-text-muted">
      {canDictate
        ? t("ai.assistant.disclaimer_voice", {
            minutes: Math.round(MAX_CHAT_RECORD_MS / 60_000),
          })
        : t("ai.assistant.disclaimer")}
    </p>
  </div>
</div>
