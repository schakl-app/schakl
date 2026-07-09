<script lang="ts">
  /**
   * Generic many-to-many "chips + type-ahead" field (CLAUDE.md §6, docs/UX.md). Renders linked
   * entities as chips sitting next to each other — the primary one brand-coloured with a ★ — and
   * a type-ahead ({@link Combobox}) to attach an existing one. Typing an unknown name offers
   * "＋ add …" via `oncreate`, so the parent can open a full create dialog.
   *
   * SSR-native: attach / detach / make-primary are real `<form method="POST" use:enhance>`
   * posts to the action URLs passed in; the page's default invalidation refreshes the list.
   * Direction-agnostic — used for contacts-on-a-client and clients-on-a-contact — so the posted
   * id field name (`idField`) and the actions come from the parent.
   */
  import { enhance } from "$app/forms";
  import { Star, X } from "@lucide/svelte";

  import Combobox from "$lib/core/ui/Combobox.svelte";

  interface LinkChip {
    id: string;
    label: string;
    hint?: string;
    is_primary: boolean;
  }
  interface Candidate {
    value: string;
    label: string;
    hint?: string;
  }

  let {
    links,
    candidates,
    idField = "id",
    linkAction,
    unlinkAction,
    primaryAction,
    placeholder = "",
    id = "linkfield",
    chipHref,
    labels,
    oncreate,
  }: {
    links: LinkChip[];
    candidates: Candidate[];
    /** Form field name for the posted id (e.g. "contact_id" or "company_id"). */
    idField?: string;
    linkAction: string;
    unlinkAction: string;
    primaryAction: string;
    placeholder?: string;
    id?: string;
    /** Optional link target for a chip's label. */
    chipHref?: (id: string) => string;
    labels: { primary: string; makePrimary: string; remove: string };
    /** Typing an unknown name offers "＋ add …", handed back here to open a create dialog. */
    oncreate?: (query: string) => void;
  } = $props();

  let comboValue = $state("");
  let pendingId = $state("");
  let linkForm: HTMLFormElement | undefined = $state();

  function onselect(value: string) {
    if (!value) return;
    pendingId = value;
    // Let the hidden input pick up `pendingId`, then submit the (enhanced) attach form.
    requestAnimationFrame(() => {
      linkForm?.requestSubmit();
      comboValue = "";
    });
  }
</script>

<div class="space-y-3">
  {#if links.length > 0}
    <ul class="flex flex-wrap gap-2">
      {#each links as chip (chip.id)}
        <li
          class="inline-flex items-center gap-1.5 rounded-full py-1 pl-2.5 pr-1.5 text-sm
            {chip.is_primary
            ? 'bg-brand/10 text-brand ring-1 ring-inset ring-brand/30'
            : 'bg-surface text-text'}"
        >
          {#if chip.is_primary}
            <!-- Colour alone can't carry meaning for a screen reader (WCAG 1.4.1). -->
            <span class="sr-only">({labels.primary})</span>
          {:else}
            <!-- These chips are navigation links, so unlike the pure pickers they cannot promote
                 on click and keep a control of their own. The primary is still marked by colour
                 alone, never a glyph (docs/UX.md). -->
            <form method="POST" action={primaryAction} use:enhance class="flex">
              <input type="hidden" name={idField} value={chip.id} />
              <button
                type="submit"
                class="text-text-muted hover:text-brand"
                title={labels.makePrimary}
                aria-label={labels.makePrimary}><Star size={13} /></button
              >
            </form>
          {/if}

          {#if chipHref}
            <a href={chipHref(chip.id)} class="font-medium hover:underline">{chip.label}</a>
          {:else}
            <span class="font-medium">{chip.label}</span>
          {/if}
          {#if chip.hint}<span class="text-xs opacity-70">{chip.hint}</span>{/if}

          <form method="POST" action={unlinkAction} use:enhance class="flex">
            <input type="hidden" name={idField} value={chip.id} />
            <button
              type="submit"
              class="rounded-full p-0.5 opacity-60 hover:bg-black/5 hover:opacity-100 dark:hover:bg-white/10"
              title={labels.remove}
              aria-label={labels.remove}><X size={14} /></button
            >
          </form>
        </li>
      {/each}
    </ul>
  {/if}

  <Combobox
    items={candidates}
    name="_link_pick"
    bind:value={comboValue}
    {id}
    {placeholder}
    allowEmpty={false}
    {onselect}
    {oncreate}
  />

  <form bind:this={linkForm} method="POST" action={linkAction} use:enhance class="hidden">
    <input type="hidden" name={idField} value={pendingId} />
  </form>
</div>
