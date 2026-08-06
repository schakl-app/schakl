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

  let {
    value,
    class: klass = "",
    images = false,
  }: {
    value: string | null | undefined;
    class?: string;
    /**
     * Draw `file:<uuid>` images — the `cid:` parts of a received e-mail, already downloaded
     * and served from our own storage. Off everywhere else: a note has no business fetching
     * pictures, and this marker is the one image form that cannot name a remote host.
     */
    images?: boolean;
  } = $props();

  let mounted = $state(false);
  onMount(() => {
    mounted = true;
  });

  const html = $derived(mounted && value ? renderMarkdown(value, { images }) : null);
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
  /* A contact mention (#165): a reference into the CRM, visually distinct from a colleague. */
  .markdown-body :global(.mention-contact) {
    background: transparent;
    box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--color-brand) 45%, transparent);
    color: var(--color-text);
  }
  /* A #task reference (#197): a deep link, so it reads as neither a person chip nor a plain
     underlined link — surface-tinted, no underline, the # is the type marker. */
  .markdown-body :global(a.mention-task) {
    background: color-mix(in srgb, var(--color-text) 8%, transparent);
    color: var(--color-text);
    text-decoration: none;
  }
  .markdown-body :global(a.mention-task:hover) {
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
  /* Same sizes as the editor's rt-prose styles — writing and reading must look identical. */
  .markdown-body :global(h3) {
    font-size: 1.15em;
  }
  .markdown-body :global(h4) {
    font-size: 1.05em;
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
  /* An e-mail's inline image (a signature logo, a pasted screenshot). Bounded, because the
     sender chose its dimensions and a 2000px letterhead must not widen the dialog. */
  .markdown-body :global(img) {
    display: inline-block;
    max-width: 100%;
    height: auto;
    max-height: 24rem;
    border-radius: 0.25rem;
  }
  /* A received mail's table is data the sender laid out; it scrolls in its own box rather
     than pushing the page sideways (docs/PERFORMANCE.md / the artifact rule, same reason). */
  .markdown-body :global(table) {
    display: block;
    overflow-x: auto;
    max-width: 100%;
    border-collapse: collapse;
  }
  .markdown-body :global(th),
  .markdown-body :global(td) {
    border: 1px solid var(--color-border);
    padding: 0.2rem 0.45rem;
    text-align: left;
  }
  .markdown-body :global(th) {
    font-weight: 600;
  }
</style>
