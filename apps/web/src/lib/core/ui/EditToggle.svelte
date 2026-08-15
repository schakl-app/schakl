<script lang="ts">
  /**
   * The edit-mode toggle that changes *shape* with the mode (#337, docs/UX.md principle 3).
   *
   * **Entering a mode is a menu item; leaving it is a button.** In use mode this is the ordinary
   * ⋯ menu, with Bewerken on top of whatever else the screen puts there (Verwijderen, …). In edit
   * mode the Bewerken item is gone and a visible **Klaar** / **Annuleren** button stands in its
   * place — because leaving is the only thing anyone wants at that moment, and a ⋯ whose single
   * item is "Klaar" is a button wearing a menu's coat.
   *
   * The menu keeps rendering in edit mode when the screen contributed items of its own; a panel
   * that contributed none (its menu *was* the toggle) shows the button alone.
   *
   * `exit` says what leaving means on this surface: `"done"` for a panel that has already saved
   * each act as it happened, `"cancel"` for a form whose Opslaan sits at the bottom — a long
   * record scrolls its own buttons out of view, so the header keeps an exit, but it must be the
   * *same* exit, never a third one hidden in the menu.
   */
  import { Check, Pencil, X } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";

  import ActionsMenu, { type ActionItem } from "./ActionsMenu.svelte";

  let {
    editing,
    onedit,
    onexit,
    canEdit = true,
    exit = "done",
    items = [],
    compact = false,
    label,
  }: {
    editing: boolean;
    /** Enter edit mode (the ⋯ item). */
    onedit: () => void;
    /** Leave edit mode (the button). */
    onexit: () => void;
    /** False hides the Bewerken item — the screen's own permission check. */
    canEdit?: boolean;
    /** "done" on a surface that saves as it goes, "cancel" on one that posts. */
    exit?: "done" | "cancel";
    /** Extra ⋯ items (delete, …), shown in both modes. */
    items?: ActionItem[];
    /** Borderless, smaller ⋯ trigger for inline panel headers. */
    compact?: boolean;
    /** aria-label for the ⋯ trigger. */
    label?: string;
  } = $props();

  const menuItems = $derived(
    editing || !canEdit
      ? items
      : [{ label: t("common.edit"), icon: Pencil, onclick: onedit }, ...items],
  );
</script>

<div class="flex shrink-0 items-center gap-2">
  {#if editing}
    {@const ExitIcon = exit === "cancel" ? X : Check}
    <button
      type="button"
      onclick={onexit}
      class="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-text hover:border-brand hover:text-brand"
    >
      <ExitIcon size={15} />
      {exit === "cancel" ? t("common.cancel") : t("common.done")}
    </button>
  {/if}
  {#if menuItems.length > 0}
    <ActionsMenu items={menuItems} {compact} {label} />
  {/if}
</div>
