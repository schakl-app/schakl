<script lang="ts">
  /**
   * Generic many-to-many "chips + type-ahead" field (CLAUDE.md §6, docs/UX.md). Renders linked
   * entities as chips sitting next to each other — the primary one marked by the brand colour and
   * nothing else, never a glyph.
   *
   * **Use mode vs edit mode** (docs/UX.md §3). Working *with* the links is the default: chips are
   * quiet navigation to the linked record, and nothing can be changed by a stray click. Changing
   * *which* records are linked is a definition change, so it lives behind the parent's edit mode
   * (`editing`). Only then do the chips become buttons — clicking one promotes it to primary, the
   * same gesture `AssigneePicker` uses — and only then do the ✕ and the type-ahead appear.
   *
   * SSR-native: attach / detach / make-primary are real `<form method="POST" use:enhance>`
   * posts to the action URLs passed in; the page's default invalidation refreshes the list.
   * Direction-agnostic — used for contacts-on-a-client and clients-on-a-contact — so the posted
   * id field name (`idField`) and the actions come from the parent.
   */
  import { enhance } from "$app/forms";
  import { X } from "@lucide/svelte";

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
    editing = false,
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
    /** Link target for a chip's label — followed in use mode only. */
    chipHref?: (id: string) => string;
    labels: { primary: string; makePrimary: string; remove: string };
    /** Attach / detach / promote are only reachable while the parent is in edit mode. */
    editing?: boolean;
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

  const chipClass = (isPrimary: boolean) =>
    `inline-flex items-center gap-1.5 rounded-full py-1 text-sm ${editing ? "pl-2.5 pr-1.5" : "px-2.5"} ` +
    (isPrimary ? "bg-brand/10 text-brand ring-1 ring-inset ring-brand/30" : "bg-surface text-text");
</script>

<div class="space-y-3">
  {#if links.length > 0}
    <ul class="flex flex-wrap gap-2">
      {#each links as chip (chip.id)}
        <li class={chipClass(chip.is_primary)}>
          {#if chip.is_primary}
            <!-- Colour alone can't carry meaning for a screen reader (WCAG 1.4.1). -->
            <span class="sr-only">({labels.primary})</span>
          {/if}

          {#if !editing && chipHref}
            <a href={chipHref(chip.id)} class="font-medium hover:underline">{chip.label}</a>
          {:else if !editing || chip.is_primary}
            <span class="font-medium">{chip.label}</span>
          {:else}
            <!-- In edit mode the chip *is* the promote control, so the marker never doubles as a
                 button and no glyph is needed. -->
            <form method="POST" action={primaryAction} use:enhance class="flex">
              <input type="hidden" name={idField} value={chip.id} />
              <button type="submit" class="font-medium hover:text-brand" title={labels.makePrimary}
                >{chip.label}</button
              >
            </form>
          {/if}
          {#if chip.hint}<span class="text-xs opacity-70">{chip.hint}</span>{/if}

          {#if editing}
            <form method="POST" action={unlinkAction} use:enhance class="flex">
              <input type="hidden" name={idField} value={chip.id} />
              <button
                type="submit"
                class="rounded-full p-0.5 opacity-60 hover:bg-black/5 hover:opacity-100 dark:hover:bg-white/10"
                title={labels.remove}
                aria-label={labels.remove}><X size={14} /></button
              >
            </form>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}

  {#if editing}
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
  {/if}
</div>
