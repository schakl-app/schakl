<script lang="ts">
  /**
   * Shared markdown editor (issues #66, #228, #255; docs/UX.md). A WYSIWYG view (Tiptap,
   * lazy-loaded) over a value that is and stays markdown *source*: headings, lists, links and
   * mention chips render styled while you type — `### `, `- `, `1. `, `**bold**` convert as you
   * type, Enter continues a list — but what is stored and submitted never stops being markdown.
   *
   * Progressive enhancement: SSR and no-JS render exactly the textarea this component replaced
   * (raw source, `name`/`form` submit unchanged). After hydration the editor chunk loads async —
   * ProseMirror never weighs on first paint — and takes over from whatever the textarea holds at
   * that moment, so a fast typist loses nothing.
   *
   * Mentions (issues #63, #103, #165, #197): the editor shows chips; the serialized value holds
   * the `@[Name](mention:<uuid>)` / `#[Title](mention:task:<uuid>)` markers, same as ever.
   */
  import {
    Bold,
    CircleStop,
    ExternalLink,
    Heading as HeadingIcon,
    Italic,
    Link as LinkIcon,
    List,
    ListOrdered,
    Sparkles,
  } from "@lucide/svelte";
  import { getContext, onMount } from "svelte";

  import { browser } from "$app/environment";
  import { AI_CONTEXT_KEY, type AIContext } from "$lib/core/ai";
  import { streamAI } from "$lib/core/ai/stream";
  import { fmtDayMonth } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { renderMarkdown } from "$lib/core/markdown";
  import Markdown from "$lib/core/ui/Markdown.svelte";
  import {
    loadMentionCandidates,
    loadTaskCandidates,
    type CandidateScope,
  } from "$lib/core/richtext/candidates";
  import type { Candidate, Editor, SuggestState } from "$lib/core/richtext/editor";

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
    scope,
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
    /** @mention candidates (issue #63): org members (default) and contacts (#165). */
    mentions?: { id: string; name: string; kind?: "user" | "contact"; subtitle?: string }[];
    /** #task reference candidates (#197), stored as `#[Title](mention:task:<uuid>)`. */
    tasks?: {
      id: string;
      name: string;
      subtitle?: string;
      assignee?: string;
      due?: string;
      overdue?: boolean;
    }[];
    /** Host context for the default candidate fetch (#237): tasks of this project/company,
     *  contacts of this company. Only read where no explicit list is passed. */
    scope?: CandidateScope;
    onchange?: (value: string) => void;
  } = $props();

  // Default candidates (issue #237): a surface that passes no lists still gets @ and # — org
  // members, host-scoped contacts and recent tasks, fetched on first focus so a page never
  // pays for an editor nobody touches (docs/PERFORMANCE.md). An explicit prop always wins
  // (the task page passes its own scoped, status-named lists).
  let armed = $state(false);
  let autoMentions = $state<Candidate[]>([]);
  let autoTasks = $state<Candidate[]>([]);
  $effect(() => {
    if (!armed) return;
    const wanted = { companyId: scope?.companyId ?? null, projectId: scope?.projectId ?? null };
    if (mentions.length === 0) {
      void loadMentionCandidates(wanted).then((found) => (autoMentions = found));
    }
    if (tasks.length === 0) {
      void loadTaskCandidates(wanted).then((found) => (autoTasks = found));
    }
  });

  // The markdown source of record: seeds the hidden input, then tracks every editor update.
  let serialized = $state(value);
  let ready = $state(false);
  let host: HTMLDivElement | undefined = $state();
  let fallback: HTMLTextAreaElement | undefined = $state();
  let editor: Editor | null = null;
  let rt: typeof import("$lib/core/richtext/editor") | null = null;
  // Bumped per transaction so the toolbar's active states re-derive.
  let tick = $state(0);

  // --- suggestion dropdown (state lives here; the plugin only reports) ---------
  let suggest = $state<SuggestState | null>(null);
  let suggestIndex = $state(0);

  const bridge = {
    onSuggestOpen: (s: SuggestState) => {
      suggest = s;
      suggestIndex = 0;
    },
    onSuggestUpdate: (s: SuggestState) => {
      suggest = s;
      suggestIndex = 0;
    },
    onSuggestKey: (event: KeyboardEvent): boolean => {
      if (!suggest || suggest.items.length === 0) {
        if (event.key === "Escape" && suggest) {
          suggest = null;
          return true;
        }
        return false;
      }
      if (event.key === "ArrowDown") {
        suggestIndex = (suggestIndex + 1) % suggest.items.length;
        return true;
      }
      if (event.key === "ArrowUp") {
        suggestIndex = (suggestIndex - 1 + suggest.items.length) % suggest.items.length;
        return true;
      }
      if (event.key === "Enter" || event.key === "Tab") {
        suggest.command(suggest.items[suggestIndex]);
        return true;
      }
      if (event.key === "Escape") {
        suggest = null;
        return true;
      }
      return false;
    },
    onSuggestClose: () => {
      suggest = null;
    },
  };

  onMount(() => {
    if (!browser) return;
    let cancelled = false;
    void (async () => {
      const mod = await import("$lib/core/richtext/editor");
      if (cancelled || !host) return;
      rt = mod;
      // Take over from whatever the textarea holds — pre-hydration typing survives.
      const source = fallback?.value ?? value;
      editor = mod.createRichTextEditor({
        element: host,
        content: source,
        placeholder,
        people: () => (mentions.length > 0 ? (mentions as Candidate[]) : autoMentions),
        tasks: () =>
          (tasks.length > 0 ? tasks : autoTasks).map((task) => ({
            ...task,
            kind: "task" as const,
          })),
        bridge,
        onUpdate: (markdown) => {
          serialized = markdown;
          onchange?.(markdown);
        },
        onTransaction: () => {
          // Deferred one microtask (#257): unmounting a still-focused editor (a save's
          // re-render) removes its DOM *before* this component's teardown runs, the browser
          // fires blur during the removal, and Tiptap dispatches the focus plugin's
          // transaction from that blur — synchronously inside Svelte's template flush, where
          // a $state write is state_unsafe_mutation. A microtask later the flush is over,
          // and after teardown `editor` is null, so a destroyed editor never writes.
          queueMicrotask(() => {
            if (!editor) return;
            tick += 1;
            // An existing-link popover follows the caret: leave the link, and it goes.
            // Insert-mode popovers stay — the caret is on plain text there by definition.
            if (linkOpen && linkExisting && !editor.isActive("link")) linkOpen = false;
          });
        },
        // Clicking a blue label is how a hidden URL is reached: open the popover prefilled.
        // No focus steal — the caret stays where the user clicked.
        onLinkClick: (href) => {
          linkExisting = true;
          linkUrl = href;
          linkOpen = true;
        },
      });
      serialized = source;
      ready = true;
    })();
    return () => {
      cancelled = true;
      editor?.destroy();
      editor = null;
    };
  });

  const active = $derived.by(() => {
    void tick;
    if (!editor) {
      return {
        bold: false,
        italic: false,
        link: false,
        heading: false,
        bullet: false,
        ordered: false,
      };
    }
    return {
      bold: editor.isActive("bold"),
      italic: editor.isActive("italic"),
      link: editor.isActive("link"),
      heading: editor.isActive("heading"),
      bullet: editor.isActive("bulletList"),
      ordered: editor.isActive("orderedList"),
    };
  });

  const toolbarButton =
    "flex h-7 w-7 items-center justify-center rounded text-text-muted hover:bg-surface hover:text-brand disabled:opacity-40";
  const activeClass = " bg-surface text-brand";

  // --- link popover -------------------------------------------------------------
  // Inline UI under the toolbar button (issue #228) — never `window.prompt`. With the caret on
  // an existing link it edits that link (prefilled URL, remove button); otherwise it inserts.
  let linkOpen = $state(false);
  let linkUrl = $state("");
  let linkExisting = $state(false);
  let linkInput: HTMLInputElement | undefined = $state();

  function toggleLink() {
    if (linkOpen) {
      linkOpen = false;
      return;
    }
    if (!editor) return;
    linkExisting = editor.isActive("link");
    linkUrl = linkExisting ? String(editor.getAttributes("link").href ?? "") : "";
    linkOpen = true;
    queueMicrotask(() => linkInput?.focus());
  }

  function insertLink() {
    const url = linkUrl.trim();
    if (!url || !editor) return;
    linkOpen = false;
    const { empty } = editor.state.selection;
    if (empty && !linkExisting) {
      // No selection to wrap: insert a labelled link and leave the caret after it — with the
      // stored mark cleared, so the very next keystroke is plain text, not more link.
      editor
        .chain()
        .focus()
        .insertContent({
          type: "text",
          text: t("richtext.link_text"),
          marks: [{ type: "link", attrs: { href: url } }],
        })
        .unsetMark("link")
        .run();
    } else {
      editor.chain().focus().extendMarkRange("link").setLink({ href: url }).run();
    }
  }

  function removeLink() {
    linkOpen = false;
    editor?.chain().focus().extendMarkRange("link").unsetLink().run();
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
    if (!editor || !rt || assist?.running) return;
    assistMenuOpen = false;
    // A selection scopes the action; no selection means the whole field (#128).
    let { from, to } = editor.state.selection;
    if (from === to) {
      from = 0;
      to = editor.state.doc.content.size;
    }
    const text = rt.serializeRange(editor, from, to).trim();
    if (!text) return;
    assist = {
      running: true,
      result: "",
      error: null,
      budget: false,
      start: from,
      end: to,
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

  /** Apply the previewed result — always an explicit act, never automatic (#128). ProseMirror's
   *  history makes the apply a single undo step. */
  function applyAssist(mode: "replace" | "insert") {
    if (!editor || !assist || assist.running || !assist.result.trim()) return;
    const html = renderMarkdown(assist.result.trim());
    const end = Math.min(assist.end, editor.state.doc.content.size);
    const start = Math.min(assist.start, end);
    if (mode === "replace") {
      editor
        .chain()
        .focus()
        .deleteRange({ from: start, to: end })
        .insertContentAt(start, html)
        .run();
    } else {
      editor.chain().focus().insertContentAt(end, html).run();
    }
    assist = null;
  }

  function cancelAssist() {
    assistAbort?.abort();
    assist = null;
    assistMenuOpen = false;
  }
</script>

<div
  class="relative rounded-lg border border-border focus-within:border-brand {klass}"
  onfocusin={() => (armed = true)}
>
  <div class="flex items-center gap-1 border-b border-border px-1.5 py-1">
    <button
      type="button"
      class={toolbarButton + (active.bold ? activeClass : "")}
      disabled={!ready}
      aria-label={t("richtext.bold")}
      title={t("richtext.bold")}
      aria-pressed={active.bold}
      onclick={() => editor?.chain().focus().toggleBold().run()}
    >
      <Bold size={15} />
    </button>
    <button
      type="button"
      class={toolbarButton + (active.italic ? activeClass : "")}
      disabled={!ready}
      aria-label={t("richtext.italic")}
      title={t("richtext.italic")}
      aria-pressed={active.italic}
      onclick={() => editor?.chain().focus().toggleItalic().run()}
    >
      <Italic size={15} />
    </button>
    <div class="relative">
      <button
        type="button"
        class={toolbarButton + (active.link ? activeClass : "")}
        disabled={!ready}
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
          class="absolute left-0 top-full z-30 mt-1 flex w-72 items-center gap-1.5 rounded-lg border border-border bg-surface-raised p-1.5 shadow-lg"
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
                editor?.chain().focus().run();
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
          {#if linkExisting}
            <a
              href={linkUrl}
              target="_blank"
              rel="noopener noreferrer nofollow"
              class="flex h-6 w-6 shrink-0 items-center justify-center rounded text-text-muted hover:text-brand"
              aria-label={t("richtext.link_open")}
              title={t("richtext.link_open")}
            >
              <ExternalLink size={14} />
            </a>
            <button
              type="button"
              class="rounded px-2 py-1 text-xs text-text-muted hover:text-red-600 dark:hover:text-red-400"
              onclick={removeLink}
            >
              {t("richtext.link_remove")}
            </button>
          {/if}
        </div>
      {/if}
    </div>
    <button
      type="button"
      class={toolbarButton + (active.heading ? activeClass : "")}
      disabled={!ready}
      aria-label={t("richtext.heading")}
      title={t("richtext.heading")}
      aria-pressed={active.heading}
      onclick={() => editor?.chain().focus().toggleHeading({ level: 3 }).run()}
    >
      <HeadingIcon size={15} />
    </button>
    <button
      type="button"
      class={toolbarButton + (active.bullet ? activeClass : "")}
      disabled={!ready}
      aria-label={t("richtext.bullet_list")}
      title={t("richtext.bullet_list")}
      aria-pressed={active.bullet}
      onclick={() => editor?.chain().focus().toggleBulletList().run()}
    >
      <List size={15} />
    </button>
    <button
      type="button"
      class={toolbarButton + (active.ordered ? activeClass : "")}
      disabled={!ready}
      aria-label={t("richtext.numbered_list")}
      title={t("richtext.numbered_list")}
      aria-pressed={active.ordered}
      onclick={() => editor?.chain().focus().toggleOrderedList().run()}
    >
      <ListOrdered size={15} />
    </button>
    {#if assistAvailable}
      <div class="relative" data-assist-menu>
        <button
          type="button"
          class={toolbarButton}
          disabled={!ready || assist?.running}
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
  </div>

  <!-- With JS the source travels in the hidden input; without, the textarea (holding raw
       source straight from SSR) keeps its name and submits as before. -->
  {#if browser && name}
    <input type="hidden" {name} {form} value={serialized} />
  {/if}

  <!-- The WYSIWYG view. The textarea below stands in until the editor chunk arrives. -->
  <div
    bind:this={host}
    id={ready ? id : undefined}
    class="rt-body {ready ? '' : 'hidden'}"
    style="--rt-min: {rows * 1.4 + 1.2}rem"
  ></div>
  {#if !ready}
    <textarea
      bind:this={fallback}
      name={browser ? null : name}
      {id}
      {form}
      {rows}
      {placeholder}
      {required}
      class="block w-full resize-y rounded-b-lg bg-transparent px-3 py-2 text-sm outline-none"
      >{value}</textarea
    >
  {/if}

  {#if assist}
    <!-- The result streams into a preview and is applied only by an explicit click (#128). -->
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

  {#if suggest && suggest.items.length > 0}
    <!-- Anchored under the editor; a phone still reaches it without a caret-precise popover. -->
    <ul
      class="absolute left-0 right-0 top-full z-30 mt-1 max-h-52 overflow-auto rounded-lg border border-border bg-surface-raised py-1 shadow-lg"
    >
      {#each suggest.items as member, i ((member.kind ?? "user") + member.id)}
        <li>
          <button
            type="button"
            class="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-surface
              {i === suggestIndex ? 'bg-surface text-brand' : 'text-text'}"
            onmousedown={(e) => {
              e.preventDefault();
              suggest?.command(member);
            }}
          >
            <span class="min-w-0 flex-1">
              <span class="block truncate">{suggest.trigger}{member.name}</span>
              {#if member.subtitle || member.assignee || member.due}
                <!-- A contact's company (#165) / a task's status, assignee and due date (#197,
                     #237): the list itself says which record you're about to pick. -->
                <span class="flex items-baseline gap-1 text-xs text-text-muted">
                  {#if member.subtitle}<span class="truncate">{member.subtitle}</span>{/if}
                  {#if member.assignee}
                    {#if member.subtitle}<span aria-hidden="true">·</span>{/if}
                    <span class="truncate">{member.assignee}</span>
                  {/if}
                  {#if member.due}
                    {#if member.subtitle || member.assignee}<span aria-hidden="true">·</span>{/if}
                    <!-- Overdue reads red here like everywhere else (docs/UX.md, Principle 4). -->
                    <span
                      class="shrink-0 tabular-nums {member.overdue
                        ? 'text-red-600 dark:text-red-400'
                        : ''}">{fmtDayMonth(member.due)}</span
                    >
                  {/if}
                </span>
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

<style>
  /* The editable document. Mirrors Markdown.svelte's reading styles so writing and reading
     look identical; `{@html}` still lives only in Markdown.svelte — Tiptap builds real DOM. */
  .rt-body :global(.rt-prose) {
    min-height: var(--rt-min);
    padding: 0.5rem 0.75rem;
    font-size: 0.875rem;
    color: var(--color-text);
    outline: none;
  }
  .rt-body :global(.rt-prose p) {
    margin: 0;
  }
  .rt-body :global(.rt-prose p + p),
  .rt-body :global(.rt-prose ul),
  .rt-body :global(.rt-prose ol),
  .rt-body :global(.rt-prose blockquote),
  .rt-body :global(.rt-prose pre),
  .rt-body :global(.rt-prose h3),
  .rt-body :global(.rt-prose h4),
  .rt-body :global(.rt-prose h5),
  .rt-body :global(.rt-prose h6) {
    margin-top: 0.5rem;
  }
  .rt-body :global(.rt-prose strong) {
    font-weight: 600;
  }
  .rt-body :global(.rt-prose em) {
    font-style: italic;
  }
  .rt-body :global(.rt-prose a) {
    color: var(--color-brand);
    text-decoration: underline;
  }
  .rt-body :global(.rt-prose ul) {
    list-style: disc;
    padding-left: 1.25rem;
  }
  .rt-body :global(.rt-prose ol) {
    list-style: decimal;
    padding-left: 1.25rem;
  }
  .rt-body :global(.rt-prose h3),
  .rt-body :global(.rt-prose h4),
  .rt-body :global(.rt-prose h5),
  .rt-body :global(.rt-prose h6) {
    font-weight: 600;
  }
  .rt-body :global(.rt-prose h3) {
    font-size: 1.15em;
  }
  .rt-body :global(.rt-prose h4) {
    font-size: 1.05em;
  }
  .rt-body :global(.rt-prose code) {
    border-radius: 0.25rem;
    background: var(--color-surface);
    padding: 0.05rem 0.3rem;
    font-size: 0.85em;
  }
  .rt-body :global(.rt-prose pre) {
    overflow-x: auto;
    border-radius: 0.5rem;
    background: var(--color-surface);
    padding: 0.6rem 0.75rem;
  }
  .rt-body :global(.rt-prose pre code) {
    background: transparent;
    padding: 0;
  }
  .rt-body :global(.rt-prose blockquote) {
    border-left: 3px solid var(--color-border);
    padding-left: 0.75rem;
    color: var(--color-text-muted);
  }
  /* @mention chip (issue #63) and its contact/task variants (#165, #197). */
  .rt-body :global(.rt-prose .mention) {
    border-radius: 0.25rem;
    background: color-mix(in srgb, var(--color-brand) 12%, transparent);
    padding: 0 0.2rem;
    font-weight: 500;
    color: var(--color-brand);
  }
  .rt-body :global(.rt-prose .mention-contact) {
    background: transparent;
    box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--color-brand) 45%, transparent);
    color: var(--color-text);
  }
  .rt-body :global(.rt-prose a.mention-task) {
    background: color-mix(in srgb, var(--color-text) 8%, transparent);
    color: var(--color-text);
    text-decoration: none;
  }
  /* Placeholder (from the Placeholder extension's data attribute). */
  .rt-body :global(.rt-prose p.is-editor-empty:first-child::before) {
    content: attr(data-placeholder);
    float: left;
    height: 0;
    pointer-events: none;
    color: var(--color-text-muted);
  }
</style>
