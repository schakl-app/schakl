<script lang="ts">
  /**
   * Shared markdown editor (issue #66, docs/UX.md). A plain `<textarea>` with a small toolbar
   * (bold / italic / link / heading / lists) and a Write ↔ Preview toggle, so the value stored is markdown *source*
   * and the user never has to type the syntax by hand. WYSIWYG was deliberately not chosen — a
   * heavy editor bundle fights the "snappy over clever" rule (docs/PERFORMANCE.md).
   *
   * Progressive enhancement: with JS off it degrades to exactly the textarea it replaces (the
   * toolbar and preview are inert), and the `name`/`form` attributes still submit the value. So it
   * drops into the existing form-action pages unchanged.
   *
   * Mentions (issue #103): the textarea shows `@Name`, never the `@[Name](mention:<uuid>)` marker.
   * The marker form is what's stored and submitted — via the hidden input (JS on) or the textarea
   * itself (JS off, which then shows raw source) — and what `onchange` reports.
   */
  import {
    Bold,
    CircleStop,
    Eye,
    Heading as HeadingIcon,
    Italic,
    Link as LinkIcon,
    List,
    ListOrdered,
    Pencil,
    Sparkles,
  } from "@lucide/svelte";
  import { getContext } from "svelte";

  import { browser } from "$app/environment";
  import { AI_CONTEXT_KEY, type AIContext } from "$lib/core/ai";
  import { streamAI } from "$lib/core/ai/stream";
  import { t } from "$lib/core/i18n";
  import Markdown from "$lib/core/ui/Markdown.svelte";

  let {
    value = "",
    name,
    id,
    rows = 4,
    form,
    placeholder = "",
    required = false,
    class: klass = "",
    mentions = [],
    tasks = [],
    onchange,
  }: {
    value?: string;
    /** `null` renders no `name` — for use outside a form, reporting via `onchange` (issue #66). */
    name?: string | null;
    id?: string;
    rows?: number;
    form?: string;
    placeholder?: string;
    required?: boolean;
    class?: string;
    /** @mention candidates (issue #63). Two kinds since #165: org members (default) and
     *  contacts, the latter with a subtitle (their company) and a distinct chip. */
    mentions?: { id: string; name: string; kind?: "user" | "contact"; subtitle?: string }[];
    /** #task reference candidates (#197): a parallel trigger keyed on `#`, matched on the task
     *  title, stored as `#[Title](mention:task:<uuid>)` and rendered as a deep link. */
    tasks?: { id: string; name: string; subtitle?: string }[];
    onchange?: (value: string) => void;
  } = $props();

  // --- mention display ↔ source mapping (issue #103) --------------------------
  // The textarea shows *display text* where a mention reads `@Name`; the stored/submitted value is
  // markdown *source* holding `@[Name](mention:<uuid>)`. `known` maps a display name back to its
  // id — filled from markers in the initial value (so a departed member's mention survives an
  // edit round-trip) and from picks. A hand-typed `@Name` that exactly matches a known member
  // serializes into a real mention; two members sharing one display name resolve to one of them —
  // the price of showing names instead of ids.
  const UUID_RE = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
  // A known display name maps back to its id *and* kind (#165): a contact's marker carries a
  // `contact:` prefix, a colleague's stays bare so pre-#165 bodies round-trip unchanged.
  const known = new Map<string, { id: string; kind: "user" | "contact" }>();
  // #task references (#197) keep their own map: `@Jan` and a task titled "Jan" must never
  // collide, so the two trigger characters never share a namespace. A plain object, like the
  // maps above it is a non-reactive cache — nothing renders from it.
  const knownTasks: Record<string, string> = {};

  function toDisplay(source: string): string {
    return source
      .replace(
        new RegExp(`@\\[([^\\]]+)\\]\\(mention:(?:(user|contact):)?(${UUID_RE})\\)`, "g"),
        (_all, mentionName: string, kind: string | undefined, id: string) => {
          known.set(mentionName, { id, kind: kind === "contact" ? "contact" : "user" });
          return `@${mentionName}`;
        },
      )
      .replace(
        new RegExp(`#\\[([^\\]]+)\\]\\(mention:task:(${UUID_RE})\\)`, "g"),
        (_all, title: string, id: string) => {
          knownTasks[title] = id;
          return `#${title}`;
        },
      );
  }

  function marker(entry: { id: string; kind: "user" | "contact" }): string {
    return entry.kind === "contact" ? `mention:contact:${entry.id}` : `mention:${entry.id}`;
  }

  function escapeRe(s: string): string {
    return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function toSource(display: string): string {
    const ids = new Map(
      mentions.map((m) => [m.name, { id: m.id, kind: (m.kind ?? "user") as "user" | "contact" }]),
    );
    for (const [n, entry] of known) ids.set(n, entry);
    let source = display;
    if (ids.size > 0) {
      // Longest name first so "Jan van Dam" beats "Jan"; same word-boundary rules as detectTrigger.
      const alternation = [...ids.keys()]
        .sort((a, b) => b.length - a.length)
        .map(escapeRe)
        .join("|");
      source = source.replace(
        new RegExp(`(^|\\s)@(${alternation})(?!\\w)`, "g"),
        (_all, pre: string, mentionName: string) =>
          `${pre}@[${mentionName}](${marker(ids.get(mentionName)!)})`,
      );
    }
    const taskIds: Record<string, string> = { ...knownTasks };
    for (const task of tasks) taskIds[task.name] = task.id;
    const taskNames = Object.keys(taskIds);
    if (taskNames.length > 0) {
      const alternation = taskNames
        .sort((a, b) => b.length - a.length)
        .map(escapeRe)
        .join("|");
      source = source.replace(
        new RegExp(`(^|\\s)#(${alternation})(?!\\w)`, "g"),
        (_all, pre: string, title: string) =>
          `${pre}#[${title}](mention:task:${taskIds[title]})`,
      );
    }
    return source;
  }

  // SSR keeps raw source in the textarea so a JS-less form still submits the markers verbatim;
  // in the browser the textarea shows display text and a hidden input carries the source.
  const initialText = (v: string) => (browser ? toDisplay(v) : v);
  let content = $state(initialText(value));
  let textarea: HTMLTextAreaElement | undefined = $state();
  let preview = $state(false);

  const serialized = $derived(toSource(content));

  function change(next: string) {
    content = next;
    onchange?.(toSource(next));
  }

  // --- @mention / #task autocomplete (issues #63, #197) -----------------------
  // A picked member is written into the source as `@[Name](mention:<uuid>)`; a picked task as
  // `#[Title](mention:task:<uuid>)`. The API extracts the id from the marker and the renderer
  // chips/links it, so nothing depends on fuzzy name matching.
  let mentionOpen = $state(false);
  let mentionQuery = $state("");
  let mentionStart = $state(-1);
  let mentionIndex = $state(0);
  // Which trigger opened the dropdown: `@` lists people, `#` lists tasks (#197).
  let mentionTrigger = $state<"@" | "#">("@");

  type Candidate = { id: string; name: string; kind?: "user" | "contact" | "task"; subtitle?: string };

  const mentionMatches = $derived<Candidate[]>(
    mentionOpen
      ? (mentionTrigger === "#"
          ? tasks.map((task) => ({ ...task, kind: "task" as const }))
          : mentions
        )
          .filter((m) => m.name.toLowerCase().includes(mentionQuery.toLowerCase()))
          .slice(0, 6)
      : [],
  );

  function detectMention() {
    const el = textarea;
    if (!el || (mentions.length === 0 && tasks.length === 0)) {
      mentionOpen = false;
      return;
    }
    const caret = el.selectionStart;
    const before = content.slice(0, caret);
    // The nearer of the two trigger characters owns the dropdown (#197).
    const at = mentions.length > 0 ? before.lastIndexOf("@") : -1;
    const hash = tasks.length > 0 ? before.lastIndexOf("#") : -1;
    const start = Math.max(at, hash);
    if (start < 0) {
      mentionOpen = false;
      return;
    }
    // The trigger must open a word (start of line or after whitespace), and the query so far must
    // hold no whitespace or bracket — otherwise the caret has moved past a mention.
    const prev = start === 0 ? " " : before[start - 1];
    const query = before.slice(start + 1);
    if (!/\s/.test(prev) || /[\s[\]]/.test(query)) {
      mentionOpen = false;
      return;
    }
    mentionTrigger = hash > at ? "#" : "@";
    mentionStart = start;
    mentionQuery = query;
    mentionIndex = 0;
    mentionOpen = true;
  }

  function pickMention(member: Candidate) {
    const el = textarea;
    if (!el) return;
    const caret = el.selectionStart;
    if (member.kind === "task") {
      knownTasks[member.name] = member.id;
    } else {
      known.set(member.name, {
        id: member.id,
        kind: member.kind === "contact" ? "contact" : "user",
      });
    }
    const token = `${member.kind === "task" ? "#" : "@"}${member.name} `;
    change(content.slice(0, mentionStart) + token + content.slice(caret));
    mentionOpen = false;
    queueMicrotask(() => {
      el.focus();
      const pos = mentionStart + token.length;
      el.selectionStart = el.selectionEnd = pos;
    });
  }

  function onMentionKeydown(event: KeyboardEvent) {
    if (!mentionOpen || mentionMatches.length === 0) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      mentionIndex = (mentionIndex + 1) % mentionMatches.length;
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      mentionIndex = (mentionIndex - 1 + mentionMatches.length) % mentionMatches.length;
    } else if (event.key === "Enter" || event.key === "Tab") {
      event.preventDefault();
      pickMention(mentionMatches[mentionIndex]);
    } else if (event.key === "Escape") {
      event.preventDefault();
      mentionOpen = false;
    }
  }

  const toolbarButton =
    "flex h-7 w-7 items-center justify-center rounded text-text-muted hover:bg-surface hover:text-brand disabled:opacity-40";

  /** Wrap the current selection (or the caret) in `before`/`after`, keeping the selection. */
  function surround(before: string, after: string = before) {
    const el = textarea;
    if (!el) return;
    const start = el.selectionStart;
    const end = el.selectionEnd;
    const selected = content.slice(start, end);
    change(content.slice(0, start) + before + selected + after + content.slice(end));
    // Restore a selection that still hugs the original text, now inside the markers.
    queueMicrotask(() => {
      el.focus();
      el.selectionStart = start + before.length;
      el.selectionEnd = end + before.length;
    });
  }

  // Any block prefix a line may already carry — stripped before a new one goes on, so the
  // heading/bullet/numbered buttons switch a line rather than stack `- ### 1.` onto it.
  const LINE_PREFIX_RE = /^(#{1,6}|[-*+]|\d+\.)\s+/;

  /** Toggle a block prefix (heading / list marker) on every line the selection touches. */
  function prefixLines(kind: "heading" | "bullet" | "numbered") {
    const el = textarea;
    if (!el) return;
    const start = el.selectionStart;
    const end = el.selectionEnd;
    const blockStart = content.lastIndexOf("\n", start - 1) + 1;
    const newlineAfter = content.indexOf("\n", end);
    const blockEnd = newlineAfter === -1 ? content.length : newlineAfter;
    const lines = content.slice(blockStart, blockEnd).split("\n");
    const marked = (line: string) =>
      kind === "heading"
        ? /^###\s/.test(line)
        : kind === "bullet"
          ? /^[-*+]\s/.test(line)
          : /^\d+\.\s/.test(line);
    const textLines = lines.filter((line) => line.trim() !== "");
    // An empty block: seed the marker at the caret so typing continues into the list/heading.
    if (textLines.length === 0) {
      const prefix = kind === "heading" ? "### " : kind === "bullet" ? "- " : "1. ";
      change(content.slice(0, start) + prefix + content.slice(end));
      queueMicrotask(() => {
        el.focus();
        el.selectionStart = el.selectionEnd = start + prefix.length;
      });
      return;
    }
    // Second press is a toggle: every marked line goes back to bare text.
    const active = textLines.every(marked);
    let n = 0;
    const next = lines
      .map((line) => {
        if (line.trim() === "") return line;
        const bare = line.replace(LINE_PREFIX_RE, "");
        if (active) return bare;
        n += 1;
        return kind === "heading" ? `### ${bare}` : kind === "bullet" ? `- ${bare}` : `${n}. ${bare}`;
      })
      .join("\n");
    change(content.slice(0, blockStart) + next + content.slice(blockEnd));
    queueMicrotask(() => {
      el.focus();
      el.selectionStart = blockStart;
      el.selectionEnd = blockStart + next.length;
    });
  }

  // --- link insertion ----------------------------------------------------------
  // An inline popover under the toolbar button (issue #228) — never `window.prompt`, which can't
  // be styled, translated consistently, or used from the PWA shell without jarring chrome.
  // The selection is captured when the popover opens: focus moves to the URL input, which would
  // otherwise collapse it.
  let linkOpen = $state(false);
  let linkUrl = $state("");
  let linkRange = { start: 0, end: 0 };
  let linkInput: HTMLInputElement | undefined = $state();

  function toggleLink() {
    if (linkOpen) {
      linkOpen = false;
      return;
    }
    const el = textarea;
    if (!el) return;
    linkRange = { start: el.selectionStart, end: el.selectionEnd };
    linkUrl = "";
    linkOpen = true;
    queueMicrotask(() => linkInput?.focus());
  }

  function insertLink() {
    const url = linkUrl.trim();
    const el = textarea;
    if (!url || !el) return;
    linkOpen = false;
    const { start, end } = linkRange;
    const label = content.slice(start, end) || t("richtext.link_text");
    const snippet = `[${label}](${url})`;
    change(content.slice(0, start) + snippet + content.slice(end));
    queueMicrotask(() => {
      el.focus();
      el.selectionStart = start + 1;
      el.selectionEnd = start + 1 + label.length;
    });
  }

  // --- writing assist (#128) --------------------------------------------------
  // Built once, here, so every module inherits it. The gate comes from the (app) layout via
  // context ("off means invisible", #126): no provider or feature off → no toolbar button.
  const aiContext = getContext<AIContext | undefined>(AI_CONTEXT_KEY);
  const assistAvailable = $derived(browser && (aiContext?.enabled("writing_assist") ?? false));

  const ASSIST_ACTIONS: { key: string; action: string; target?: string }[] = [
    { key: "improve", action: "improve" },
    { key: "shorten", action: "shorten" },
    { key: "expand", action: "expand" },
    { key: "fix", action: "fix" },
    { key: "tone_business", action: "tone_business" },
    { key: "tone_informal", action: "tone_informal" },
    { key: "translate_nl", action: "translate", target: "nl" },
    { key: "translate_en", action: "translate", target: "en" },
    { key: "draft", action: "draft" },
  ];

  let assistMenuOpen = $state(false);
  let assist = $state<{
    running: boolean;
    result: string;
    error: string | null;
    budget: boolean;
    start: number;
    end: number;
    request: { action: string; target?: string };
  } | null>(null);
  let assistAbort: AbortController | null = null;

  async function runAssist(item: { action: string; target?: string }, override = false) {
    const el = textarea;
    if (!el || assist?.running) return;
    assistMenuOpen = false;
    // A selection scopes the action; no selection means the whole field (#128).
    let start = el.selectionStart;
    let end = el.selectionEnd;
    if (start === end) {
      start = 0;
      end = content.length;
    }
    const text = toSource(content.slice(start, end)).trim();
    if (!text) return;
    assist = {
      running: true,
      result: "",
      error: null,
      budget: false,
      start,
      end,
      request: item,
    };
    assistAbort = new AbortController();
    try {
      const failure = await streamAI(
        "assist/write",
        {
          action: item.action,
          text,
          target_locale: item.target ?? null,
          override_budget: override,
        },
        {
          onText: (delta) => {
            if (assist) assist.result += delta;
          },
          onError: (_code, message) => {
            if (assist) assist.error = message;
          },
        },
        assistAbort.signal,
      );
      if (failure && assist) {
        if (failure.code === "ai_budget_reached") assist.budget = true;
        else assist.error = failure.message;
      }
    } catch (err) {
      if (assist && !(err instanceof DOMException && err.name === "AbortError")) {
        assist.error = "errors.ai_provider_error";
      }
    } finally {
      if (assist) assist.running = false;
      assistAbort = null;
    }
  }

  /** Apply the previewed result — always an explicit act, never automatic (#128). Uses
   *  `insertText` so the native undo stack survives the apply. */
  function applyAssist(mode: "replace" | "insert") {
    const el = textarea;
    if (!el || !assist || assist.running || !assist.result.trim()) return;
    const display = toDisplay(assist.result.trim());
    const insertion = mode === "insert" ? `\n\n${display}` : display;
    el.focus();
    if (mode === "replace") el.setSelectionRange(assist.start, assist.end);
    else el.setSelectionRange(assist.end, assist.end);
    if (!document.execCommand("insertText", false, insertion)) {
      const from = mode === "replace" ? assist.start : assist.end;
      change(content.slice(0, from) + insertion + content.slice(assist.end));
    }
    assist = null;
  }

  function cancelAssist() {
    assistAbort?.abort();
    assist = null;
    assistMenuOpen = false;
  }
</script>

<div class="relative rounded-lg border border-border focus-within:border-brand {klass}">
  <div class="flex items-center gap-1 border-b border-border px-1.5 py-1">
    <button
      type="button"
      class={toolbarButton}
      disabled={preview}
      aria-label={t("richtext.bold")}
      title={t("richtext.bold")}
      onclick={() => surround("**")}
    >
      <Bold size={15} />
    </button>
    <button
      type="button"
      class={toolbarButton}
      disabled={preview}
      aria-label={t("richtext.italic")}
      title={t("richtext.italic")}
      onclick={() => surround("_")}
    >
      <Italic size={15} />
    </button>
    <div class="relative">
      <button
        type="button"
        class={toolbarButton}
        disabled={preview}
        aria-label={t("richtext.link")}
        title={t("richtext.link")}
        aria-haspopup="dialog"
        aria-expanded={linkOpen}
        onclick={toggleLink}
      >
        <LinkIcon size={15} />
      </button>
      {#if linkOpen}
        <div
          class="absolute left-0 top-full z-30 mt-1 flex w-64 items-center gap-1.5 rounded-lg border border-border bg-surface-raised p-1.5 shadow-lg"
          role="dialog"
          aria-label={t("richtext.link_prompt")}
        >
          <input
            bind:this={linkInput}
            bind:value={linkUrl}
            type="url"
            placeholder={t("richtext.link_prompt")}
            aria-label={t("richtext.link_prompt")}
            class="min-w-0 flex-1 rounded border border-border bg-transparent px-2 py-1 text-sm outline-none focus:border-brand"
            onkeydown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                insertLink();
              } else if (e.key === "Escape") {
                e.preventDefault();
                linkOpen = false;
                textarea?.focus();
              }
            }}
          />
          <button
            type="button"
            class="rounded bg-brand px-2 py-1 text-xs font-medium text-white hover:opacity-90"
            onclick={insertLink}
          >
            {t("common.add")}
          </button>
        </div>
      {/if}
    </div>
    <button
      type="button"
      class={toolbarButton}
      disabled={preview}
      aria-label={t("richtext.heading")}
      title={t("richtext.heading")}
      onclick={() => prefixLines("heading")}
    >
      <HeadingIcon size={15} />
    </button>
    <button
      type="button"
      class={toolbarButton}
      disabled={preview}
      aria-label={t("richtext.bullet_list")}
      title={t("richtext.bullet_list")}
      onclick={() => prefixLines("bullet")}
    >
      <List size={15} />
    </button>
    <button
      type="button"
      class={toolbarButton}
      disabled={preview}
      aria-label={t("richtext.numbered_list")}
      title={t("richtext.numbered_list")}
      onclick={() => prefixLines("numbered")}
    >
      <ListOrdered size={15} />
    </button>
    {#if assistAvailable}
      <div class="relative" data-assist-menu>
        <button
          type="button"
          class={toolbarButton}
          disabled={preview || assist?.running}
          aria-label={t("ai.assist.title")}
          title={t("ai.assist.title")}
          aria-haspopup="menu"
          aria-expanded={assistMenuOpen}
          onclick={() => (assistMenuOpen = !assistMenuOpen)}
        >
          <Sparkles size={15} />
        </button>
        {#if assistMenuOpen}
          <ul
            class="absolute left-0 top-full z-30 mt-1 w-52 rounded-lg border border-border bg-surface-raised py-1 shadow-lg"
            role="menu"
          >
            {#each ASSIST_ACTIONS as item (item.key)}
              <li>
                <button
                  type="button"
                  role="menuitem"
                  class="block w-full px-3 py-1.5 text-left text-sm text-text hover:bg-surface"
                  onclick={() => void runAssist(item)}
                >
                  {t(`ai.assist.${item.key}`)}
                </button>
              </li>
            {/each}
          </ul>
        {/if}
      </div>
    {/if}
    <div class="ml-auto">
      <button
        type="button"
        class="flex h-7 items-center gap-1 rounded px-2 text-xs text-text-muted hover:bg-surface hover:text-brand"
        aria-pressed={preview}
        onclick={() => (preview = !preview)}
      >
        {#if preview}
          <Pencil size={13} /> {t("richtext.write")}
        {:else}
          <Eye size={13} /> {t("richtext.preview")}
        {/if}
      </button>
    </div>
  </div>

  {#if preview}
    <div class="min-h-[4rem] px-3 py-2">
      {#if content.trim()}
        <Markdown value={serialized} />
      {:else}
        <p class="text-sm text-text-muted">{t("richtext.preview_empty")}</p>
      {/if}
    </div>
  {/if}
  <!-- The textarea stays mounted (just hidden) in preview so its value is always submitted. -->
  <!-- With JS the source travels in the hidden input; without, the textarea (holding raw
       source straight from SSR) keeps its name and submits as before. -->
  {#if browser && name}
    <input type="hidden" {name} {form} value={serialized} />
  {/if}
  <textarea
    bind:this={textarea}
    bind:value={content}
    oninput={(e) => {
      onchange?.(toSource(e.currentTarget.value));
      detectMention();
    }}
    onkeydown={onMentionKeydown}
    onblur={() => queueMicrotask(() => (mentionOpen = false))}
    name={browser ? null : name}
    {id}
    {form}
    {rows}
    {placeholder}
    {required}
    class="block w-full resize-y rounded-b-lg bg-transparent px-3 py-2 text-sm outline-none {preview
      ? 'hidden'
      : ''}"></textarea>

  {#if assist}
    <!-- The result streams into a preview and is applied only by an explicit click; the
         native undo stack survives the apply (#128). -->
    <div class="border-t border-border px-3 py-2">
      <p class="mb-1 flex items-center gap-1.5 text-xs font-medium text-text-muted">
        <Sparkles size={12} />
        {t(
          `ai.assist.${assist.request.target ? `${assist.request.action}_${assist.request.target}` : assist.request.action}`,
        )}
        {#if assist.running}<span class="animate-pulse">…</span>{/if}
      </p>
      {#if assist.result}
        <div class="max-h-56 overflow-y-auto rounded-lg bg-surface px-3 py-2 text-sm">
          <Markdown value={assist.result} />
        </div>
      {/if}
      {#if assist.error}
        <p class="text-sm text-red-600 dark:text-red-400">{t(assist.error)}</p>
      {/if}
      {#if assist.budget}
        <p class="text-sm text-amber-700 dark:text-amber-400">
          {t("ai.budget_notice")}
          <button
            type="button"
            class="underline"
            onclick={() => void runAssist(assist!.request, true)}>{t("ai.budget_proceed")}</button
          >
        </p>
      {/if}
      <div class="mt-2 flex items-center gap-2">
        {#if assist.running}
          <button
            type="button"
            class="flex items-center gap-1 rounded-lg border border-border px-2.5 py-1 text-xs text-text hover:border-brand"
            onclick={() => assistAbort?.abort()}
          >
            <CircleStop size={13} />
            {t("ai.assist.stop")}
          </button>
        {:else if assist.result.trim()}
          <button
            type="button"
            class="rounded-lg bg-brand px-2.5 py-1 text-xs font-medium text-white hover:opacity-90"
            onclick={() => applyAssist("replace")}>{t("ai.assist.replace")}</button
          >
          <button
            type="button"
            class="rounded-lg border border-border px-2.5 py-1 text-xs text-text hover:border-brand"
            onclick={() => applyAssist("insert")}>{t("ai.assist.insert")}</button
          >
        {/if}
        <button
          type="button"
          class="rounded-lg px-2.5 py-1 text-xs text-text-muted hover:text-text"
          onclick={cancelAssist}>{t("common.cancel")}</button
        >
      </div>
    </div>
  {/if}

  {#if mentionOpen && mentionMatches.length > 0}
    <!-- Anchored under the editor; a phone still reaches it without a caret-precise popover. -->
    <ul
      class="absolute left-0 right-0 top-full z-30 mt-1 max-h-52 overflow-auto rounded-lg border border-border bg-surface-raised py-1 shadow-lg"
    >
      {#each mentionMatches as member, i ((member.kind ?? "user") + member.id)}
        <li>
          <button
            type="button"
            class="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-surface
              {i === mentionIndex ? 'bg-surface text-brand' : 'text-text'}"
            onmousedown={(e) => {
              e.preventDefault();
              pickMention(member);
            }}
          >
            <span class="min-w-0 flex-1">
              <span class="block truncate"
                >{member.kind === "task" ? "#" : "@"}{member.name}</span
              >
              {#if member.subtitle}
                <!-- A contact's company (#165) / a task's context (#197): the list itself says
                     which kind you're picking. -->
                <span class="block truncate text-xs text-text-muted">{member.subtitle}</span>
              {/if}
            </span>
            {#if member.kind === "contact"}
              <span
                class="shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-medium text-text-muted ring-1 ring-inset ring-border"
              >
                {t("common.mention_contact")}
              </span>
            {:else if member.kind === "task"}
              <span
                class="shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-medium text-text-muted ring-1 ring-inset ring-border"
              >
                {t("common.mention_task")}
              </span>
            {/if}
          </button>
        </li>
      {/each}
    </ul>
  {/if}
</div>
