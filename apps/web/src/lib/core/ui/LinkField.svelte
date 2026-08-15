<script lang="ts">
  /**
   * Generic many-to-many "chips + type-ahead" field (CLAUDE.md §6, docs/UX.md). Renders linked
   * entities as chips sitting next to each other — the primary one marked by a ★ **and** the
   * brand colour. Colour alone was the original design and it was wrong twice over: on a tenant
   * whose brand is gold the chip is indistinguishable from an amber warning, so the person you
   * should ring first read as a problem; and nothing at all reaches a screen reader (WCAG 1.4.1).
   *
   * The ★ says *that* a chip is special; it does not say **what** or **which direction** (#374).
   * Two things carry that in words. Every chip has a `title`, and the marked one's names what it
   * is — the shape `Assignees` already uses — so a client with a single contact person, where
   * there is no grey chip to contrast against, still has an answer. And `hint` states the promote
   * gesture in edit mode, because clicking a chip to re-designate is discoverable only by hovering
   * a thing you did not know to hover.
   *
   * Both `labels.primary` and `labels.makePrimary` are the *parent's* words, and on a
   * direction-ambiguous surface they have to say the direction: `company_contacts.is_primary`
   * means "the primary contact **for that company**", so the clients-on-a-contact side reads
   * "hoofdcontact bij deze klant", never a bare "primair" that invites the reading — which does
   * not exist — that this is the person's own main client.
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
    editing = false,
    hint,
    oncreate,
    onsearch,
    searching = false,
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
    /** One line naming the promote gesture, shown while editing. Omit and nothing renders. */
    hint?: string;
    /** Typing an unknown name offers "＋ add …", handed back here to open a create dialog. */
    oncreate?: (query: string) => void;
    /** Search the candidate set server-side rather than shipping all of it (#290) — see
     *  `Combobox`. Omit and the picker filters `candidates` in the browser as before. */
    onsearch?: (query: string) => void;
    /** A candidate search is in flight (server-search pickers only). */
    searching?: boolean;
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
        <!-- The title sits on the <li>, not on the overlay: tooltip lookup walks up the tree, so
             the navigation <a> (which has none of its own) inherits it, while edit mode's promote
             button keeps its own more specific one. -->
        <li
          class={chipClass(chip.is_primary)}
          title={chip.is_primary ? `${chip.label} · ${labels.primary}` : chip.label}
        >
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

          <span class="pointer-events-none flex items-center gap-1 font-medium">
            {#if chip.is_primary}
              <!-- A glyph, not only a colour. The brand is gold on some tenants, which renders
                   identically to an amber warning chip — so the primary contact read as a
                   problem rather than as the person to ring first. Colour alone also carries
                   nothing for a screen reader (WCAG 1.4.1), hence the label beside it. -->
              <Star size={12} class="shrink-0 fill-current" aria-hidden="true" />
              <span class="sr-only">({labels.primary})</span>
            {/if}
            {chip.label}
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
    {#if hint && links.length > 0}
      <!-- Only with chips on screen: over an empty list it would describe a gesture there is
           nothing to perform. -->
      <p class="text-xs text-text-muted">{hint}</p>
    {/if}
    <Combobox
      items={candidates}
      name="_link_pick"
      bind:value={comboValue}
      {id}
      {placeholder}
      allowEmpty={false}
      {onselect}
      keepOpenOnSelect
      {oncreate}
      {onsearch}
      {searching}
    />

    <form bind:this={linkForm} method="POST" action={linkAction} use:enhance class="hidden">
      <input type="hidden" name={idField} value={pendingId} />
    </form>
  {/if}
</div>
