<script lang="ts">
  /**
   * Shared markdown editor (issue #66, docs/UX.md). A plain `<textarea>` with a small toolbar
   * (bold / italic / link) and a Write ↔ Preview toggle, so the value stored is markdown *source*
   * and the user never has to type the syntax by hand. WYSIWYG was deliberately not chosen — a
   * heavy editor bundle fights the "snappy over clever" rule (docs/PERFORMANCE.md).
   *
   * Progressive enhancement: with JS off it degrades to exactly the textarea it replaces (the
   * toolbar and preview are inert), and the `name`/`form` attributes still submit the value. So it
   * drops into the existing form-action pages unchanged.
   */
  import { Bold, Eye, Italic, Link as LinkIcon, Pencil } from "@lucide/svelte";

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
    /** @mention candidates (issue #63): typing `@` autocompletes against these org members. */
    mentions?: { id: string; name: string }[];
    onchange?: (value: string) => void;
  } = $props();

  let content = $state(value);
  let textarea: HTMLTextAreaElement | undefined = $state();
  let preview = $state(false);

  function change(next: string) {
    content = next;
    onchange?.(next);
  }

  // --- @mention autocomplete (issue #63) -------------------------------------
  // A picked member is written into the source as `@[Name](mention:<uuid>)`; the API extracts the
  // id from that marker and the renderer chips it, so nothing depends on fuzzy name matching.
  let mentionOpen = $state(false);
  let mentionQuery = $state("");
  let mentionStart = $state(-1);
  let mentionIndex = $state(0);

  const mentionMatches = $derived(
    mentionOpen
      ? mentions
          .filter((m) => m.name.toLowerCase().includes(mentionQuery.toLowerCase()))
          .slice(0, 6)
      : [],
  );

  function detectMention() {
    const el = textarea;
    if (!el || mentions.length === 0) {
      mentionOpen = false;
      return;
    }
    const caret = el.selectionStart;
    const before = content.slice(0, caret);
    const at = before.lastIndexOf("@");
    if (at < 0) {
      mentionOpen = false;
      return;
    }
    // The `@` must open a word (start of line or after whitespace), and the query so far must hold
    // no whitespace or bracket — otherwise the caret has moved past a mention.
    const prev = at === 0 ? " " : before[at - 1];
    const query = before.slice(at + 1);
    if (!/\s/.test(prev) || /[\s[\]]/.test(query)) {
      mentionOpen = false;
      return;
    }
    mentionStart = at;
    mentionQuery = query;
    mentionIndex = 0;
    mentionOpen = true;
  }

  function pickMention(member: { id: string; name: string }) {
    const el = textarea;
    if (!el) return;
    const caret = el.selectionStart;
    const token = `@[${member.name}](mention:${member.id}) `;
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

  function insertLink() {
    const el = textarea;
    if (!el) return;
    const url = window.prompt(t("richtext.link_prompt"));
    if (!url) return;
    const start = el.selectionStart;
    const end = el.selectionEnd;
    const label = content.slice(start, end) || t("richtext.link_text");
    const snippet = `[${label}](${url})`;
    change(content.slice(0, start) + snippet + content.slice(end));
    queueMicrotask(() => {
      el.focus();
      el.selectionStart = start + 1;
      el.selectionEnd = start + 1 + label.length;
    });
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
    <button
      type="button"
      class={toolbarButton}
      disabled={preview}
      aria-label={t("richtext.link")}
      title={t("richtext.link")}
      onclick={insertLink}
    >
      <LinkIcon size={15} />
    </button>
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
        <Markdown value={content} />
      {:else}
        <p class="text-sm text-text-muted">{t("richtext.preview_empty")}</p>
      {/if}
    </div>
  {/if}
  <!-- The textarea stays mounted (just hidden) in preview so its value is always submitted. -->
  <textarea
    bind:this={textarea}
    bind:value={content}
    oninput={(e) => {
      onchange?.(e.currentTarget.value);
      detectMention();
    }}
    onkeydown={onMentionKeydown}
    onblur={() => queueMicrotask(() => (mentionOpen = false))}
    {name}
    {id}
    {form}
    {rows}
    {placeholder}
    {required}
    class="block w-full resize-y rounded-b-lg bg-transparent px-3 py-2 text-sm outline-none {preview
      ? 'hidden'
      : ''}"></textarea>

  {#if mentionOpen && mentionMatches.length > 0}
    <!-- Anchored under the editor; a phone still reaches it without a caret-precise popover. -->
    <ul
      class="absolute left-0 right-0 top-full z-30 mt-1 max-h-52 overflow-auto rounded-lg border border-border bg-surface-raised py-1 shadow-lg"
    >
      {#each mentionMatches as member, i (member.id)}
        <li>
          <button
            type="button"
            class="flex w-full items-center px-3 py-1.5 text-left text-sm hover:bg-surface
              {i === mentionIndex ? 'bg-surface text-brand' : 'text-text'}"
            onmousedown={(e) => {
              e.preventDefault();
              pickMention(member);
            }}
          >
            @{member.name}
          </button>
        </li>
      {/each}
    </ul>
  {/if}
</div>
