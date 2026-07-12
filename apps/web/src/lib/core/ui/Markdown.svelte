<script lang="ts">
  /**
   * The one component that renders user markdown, and the only sanctioned `{@html}` site in the
   * app (issue #66, docs/UX.md). Everything it prints has been through DOMPurify in
   * `renderMarkdown`. Never inline `{@html}` elsewhere — route it through here.
   *
   * SSR / no-JS: DOMPurify needs a DOM, so before mount (and with JS off) we render the escaped
   * source in `whitespace-pre-wrap` — readable, safe, and identical to the pre-#66 behaviour. The
   * flip to rendered HTML happens after `onMount`, so there is no hydration mismatch.
   */
  import { onMount } from "svelte";

  import { renderMarkdown } from "$lib/core/markdown";

  let { value, class: klass = "" }: { value: string | null | undefined; class?: string } = $props();

  let mounted = $state(false);
  onMount(() => {
    mounted = true;
  });

  const html = $derived(mounted && value ? renderMarkdown(value) : null);
</script>

{#if html !== null}
  <!-- eslint-disable-next-line svelte/no-at-html-tags -- sanitized in renderMarkdown (issue #66) -->
  <div class="markdown-body text-sm text-text {klass}">{@html html}</div>
{:else if value}
  <p class="markdown-body whitespace-pre-wrap text-sm text-text {klass}">{value}</p>
{/if}

<style>
  /* Tailwind's reset strips list/heading/link styling; `{@html}` children don't get the
     component's scope class, so restore the essentials with `:global` under `.markdown-body`. */
  .markdown-body :global(p) {
    margin: 0;
  }
  .markdown-body :global(p + p),
  .markdown-body :global(ul),
  .markdown-body :global(ol),
  .markdown-body :global(blockquote),
  .markdown-body :global(pre) {
    margin-top: 0.5rem;
  }
  .markdown-body :global(strong) {
    font-weight: 600;
  }
  .markdown-body :global(em) {
    font-style: italic;
  }
  .markdown-body :global(a) {
    color: var(--color-brand);
    text-decoration: underline;
  }
  /* @mention chip (issue #63). */
  .markdown-body :global(.mention) {
    border-radius: 0.25rem;
    background: color-mix(in srgb, var(--color-brand) 12%, transparent);
    padding: 0 0.2rem;
    font-weight: 500;
    color: var(--color-brand);
  }
  .markdown-body :global(ul) {
    list-style: disc;
    padding-left: 1.25rem;
  }
  .markdown-body :global(ol) {
    list-style: decimal;
    padding-left: 1.25rem;
  }
  .markdown-body :global(h3),
  .markdown-body :global(h4),
  .markdown-body :global(h5),
  .markdown-body :global(h6) {
    font-weight: 600;
    margin-top: 0.5rem;
  }
  .markdown-body :global(code) {
    border-radius: 0.25rem;
    background: var(--color-surface);
    padding: 0.05rem 0.3rem;
    font-size: 0.85em;
  }
  .markdown-body :global(pre) {
    overflow-x: auto;
    border-radius: 0.5rem;
    background: var(--color-surface);
    padding: 0.6rem 0.75rem;
  }
  .markdown-body :global(pre code) {
    background: transparent;
    padding: 0;
  }
  .markdown-body :global(blockquote) {
    border-left: 3px solid var(--color-border);
    padding-left: 0.75rem;
    color: var(--color-text-muted);
  }
</style>
