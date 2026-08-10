<script lang="ts">
  /**
   * A block of text the user is meant to take somewhere else — a shell command, an endpoint,
   * a header — with a copy button on it.
   *
   * Three screens had each grown their own version of this (Instellingen → SSO's callback URL,
   * → Domein's DNS records, → Mollie's webhook), and Instellingen → API en MCP needs four on
   * one page. The wrapping matters as much as the button: a `claude mcp add` line with a
   * 48-character secret in it is wider than any column this app has, so it wraps rather than
   * scrolling sideways — a command you cannot see the end of is one you cannot check before
   * you run it.
   */
  import { Check, Copy } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";

  let {
    value,
    label,
    help,
  }: {
    /** The exact text copied to the clipboard — what is shown is what is taken. */
    value: string;
    label?: string;
    help?: string;
  } = $props();

  let copied = $state(false);
  let timer: ReturnType<typeof setTimeout> | undefined;

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      copied = true;
      clearTimeout(timer);
      timer = setTimeout(() => (copied = false), 1600);
    } catch {
      // A denied clipboard permission leaves the text selectable, which is the fallback.
      copied = false;
    }
  }
</script>

<div>
  {#if label}
    <p class="mb-1 text-xs font-medium text-text">{label}</p>
  {/if}
  <div class="flex items-start gap-2 rounded-lg border border-border bg-surface p-2">
    <code class="min-w-0 grow whitespace-pre-wrap break-all font-mono text-xs text-text"
      >{value}</code
    >
    <button
      type="button"
      onclick={copy}
      aria-label={t("common.copy")}
      class="flex shrink-0 items-center gap-1 rounded-lg border border-border px-2 py-1 text-xs text-text-muted hover:border-brand hover:text-text"
    >
      {#if copied}
        <Check size={13} />
        {t("common.copied")}
      {:else}
        <Copy size={13} />
        {t("common.copy")}
      {/if}
    </button>
  </div>
  {#if help}
    <p class="mt-1 text-xs text-text-muted">{help}</p>
  {/if}
</div>
