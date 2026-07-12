<script lang="ts">
  /**
   * The contextual assistant (#127): ask about the tenant's own data, grounded and cited.
   * Lives in a SlideOver opened from the shell; inherits the page's entity as a removable
   * chip. Conversations are per-user and ephemeral (v1) — the transcript lives here.
   */
  import { CircleStop, SendHorizontal, Sparkles, X } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";
  import Markdown from "$lib/core/ui/Markdown.svelte";

  import { sourceHref, type AISource, type AssistantEntity } from "./index";
  import { streamAI } from "./stream";

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

  const activeContext = $derived(useContext && context ? context : null);
  const suggestions = $derived.by(() => {
    const kind = activeContext?.entity_type;
    if (kind === "company" || kind === "project" || kind === "task") {
      return [t(`ai.assistant.suggest_${kind}_1`), t(`ai.assistant.suggest_${kind}_2`)];
    }
    return [t("ai.assistant.suggest_generic_1"), t("ai.assistant.suggest_generic_2")];
  });

  function toolLabel(name: string): string {
    const key = `ai.assistant.searching_${name.split(".")[0]}`;
    const label = t(key);
    return label === key ? t("ai.assistant.searching") : label;
  }

  async function send(text: string, overrideBudget = false) {
    const question = text.trim();
    if (!question || streaming) return;
    error = null;
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
          onTool: (name) => {
            toolStatus = toolLabel(name);
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
    <p class="mt-1.5 text-[11px] text-text-muted">{t("ai.assistant.disclaimer")}</p>
  </div>
</div>
