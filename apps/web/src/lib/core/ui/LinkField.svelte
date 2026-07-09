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

  /** In use mode a chip navigates (when it has an href); in edit mode it promotes, unless it
   *  already is the primary. Only a chip that does something gets a hover. */
  const isClickable = (isPrimary: boolean) => (editing ? !isPrimary : Boolean(chipHref));

  const chipClass = (isPrimary: boolean) => {
    const base = `relative inline-flex items-center gap-1.5 rounded-full py-1 text-sm transition-colors ${
      editing ? "pl-2.5 pr-1.5" : "px-2.5"
    }`;
    if (isPrimary) {
      // Already brand-coloured, so its hover deepens the ring rather than changing the fill.
      const hover = isClickable(true) ? "hover:ring-brand/60" : "";
      return `${base} bg-brand/10 text-brand ring-1 ring-inset ring-brand/30 ${hover}`;
    }
    // A grey chip's hover previews the brand colour it takes when clicked — a promotion in edit
    // mode, and in use mode simply the affordance that it leads somewhere.
    const hover = isClickable(false)
      ? "hover:bg-brand/10 hover:text-brand hover:ring-1 hover:ring-inset hover:ring-brand/30"
      : "";
    return `${base} bg-surface text-text ${hover}`;
  };
</script>

<div class="space-y-3">
  {#if links.length > 0}
    <ul class="flex flex-wrap gap-2">
      {#each links as chip (chip.id)}
        <li class={chipClass(chip.is_primary)}>
          <!-- The whole chip is the target — navigation in use mode, promote in edit mode —
               stretched over the pill rather than wrapping it, since the ✕ is a control of its own
               and anchors/buttons cannot nest. -->
          {#if !editing && chipHref}
            <a
              href={chipHref(chip.id)}
              class="absolute inset-0 rounded-full"
              aria-label={chip.label}
            ></a>
          {:else if editing && !chip.is_primary}
            <form method="POST" action={primaryAction} use:enhance class="absolute inset-0">
              <input type="hidden" name={idField} value={chip.id} />
              <button
                type="submit"
                class="h-full w-full cursor-pointer rounded-full"
                title={labels.makePrimary}
                aria-label={labels.makePrimary}
              ></button>
            </form>
          {/if}

          <span class="pointer-events-none font-medium">
            {chip.label}
            {#if chip.is_primary}
              <!-- Colour alone can't carry meaning for a screen reader (WCAG 1.4.1). -->
              <span class="sr-only">({labels.primary})</span>
            {/if}
          </span>
          {#if chip.hint}
            <span class="pointer-events-none text-xs opacity-70">{chip.hint}</span>
          {/if}

          {#if editing}
            <form method="POST" action={unlinkAction} use:enhance class="relative flex">
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
