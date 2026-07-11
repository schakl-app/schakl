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
    onchange?: (value: string) => void;
  } = $props();

  let content = $state(value);
  let textarea: HTMLTextAreaElement | undefined = $state();
  let preview = $state(false);

  function change(next: string) {
    content = next;
    onchange?.(next);
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

<div class="rounded-lg border border-border focus-within:border-brand {klass}">
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
    oninput={(e) => onchange?.(e.currentTarget.value)}
    {name}
    {id}
    {form}
    {rows}
    {placeholder}
    {required}
    class="block w-full resize-y rounded-b-lg bg-transparent px-3 py-2 text-sm outline-none {preview
      ? 'hidden'
      : ''}"></textarea>
</div>
