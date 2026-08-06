<script lang="ts">
  /**
   * One section's paragraph on the review screen (issue #300): read it, fix it, or ask for
   * another draft.
   *
   * Use-vs-edit (docs/UX.md): the text reads as prose until somebody presses Bewerken. That
   * matters more here than on most screens — the reviewer's job is to *read* what the client
   * will read, and a page of textareas is not that.
   *
   * `busy.keep()` on the save form, because it edits what already exists: the SvelteKit default
   * would reset the textarea to its (empty) `defaultValue` and write that emptiness back — the
   * exact data loss `pnpm forms:check` exists to prevent.
   */
  import { Check, Pencil, Sparkles, X } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";

  let {
    sectionKey,
    title,
    text = "",
    edited = false,
    canWrite = false,
    canUseAi = true,
  }: {
    sectionKey: string;
    title: string;
    text?: string;
    /** A human has rewritten this one; a regenerate will leave it alone. Worth saying so. */
    edited?: boolean;
    canWrite?: boolean;
    canUseAi?: boolean;
  } = $props();

  let editing = $state(false);
  // Writable derived: seeded from the prop and re-seeded whenever the server sends new text
  // (a rewrite landed), while still being typeable.
  let draft = $derived(text);

  const busy = new InFlight();
</script>

<section class="rounded-xl border border-border bg-surface-raised p-4">
  <header class="mb-2 flex items-center gap-2">
    <h3 class="text-sm font-semibold text-text">{title}</h3>
    {#if edited}
      <span class="rounded-full bg-surface px-2 py-0.5 text-[11px] text-text-muted">
        {t("reporting.review.edited")}
      </span>
    {/if}
    {#if canWrite && !editing}
      <div class="ml-auto flex items-center gap-1">
        {#if canUseAi}
          <form method="POST" action="?/rewrite" use:enhance={busy.keep("ai")}>
            <input type="hidden" name="section_key" value={sectionKey} />
            <Button
              type="submit"
              variant="secondary"
              size="xs"
              loading={busy.is("ai")}
              disabled={busy.active}
            >
              <Sparkles size={13} />
              {t("reporting.review.rewrite")}
            </Button>
          </form>
        {/if}
        <button
          type="button"
          class="rounded-lg p-1.5 text-text-muted hover:bg-surface hover:text-text"
          aria-label={t("common.edit")}
          onclick={() => (editing = true)}
        >
          <Pencil size={14} />
        </button>
      </div>
    {/if}
  </header>

  {#if editing}
    <!-- `reset: false` keeps what was typed (it *is* the saved value now), and the editor
         closes on success so the reviewer lands back on the prose they are checking. -->
    <form
      method="POST"
      action="?/narrative"
      use:enhance={busy.wrap("save", () => async ({ update }) => {
        await update({ reset: false });
        editing = false;
      })}
      class="space-y-2"
    >
      <input type="hidden" name="section_key" value={sectionKey} />
      <textarea
        name="text"
        bind:value={draft}
        rows={6}
        class="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm leading-relaxed text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand"
      ></textarea>
      <div class="flex items-center gap-2">
        <Button type="submit" size="sm" loading={busy.is("save")} disabled={busy.active}>
          <Check size={14} />
          {t("common.save")}
        </Button>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onclick={() => {
            draft = text;
            editing = false;
          }}
        >
          <X size={14} />
          {t("common.cancel")}
        </Button>
      </div>
    </form>
  {:else if text}
    <p class="whitespace-pre-line text-sm leading-relaxed text-text-muted">{text}</p>
  {:else}
    <p class="text-sm italic text-text-muted">{t("reporting.review.no_text")}</p>
  {/if}
</section>
