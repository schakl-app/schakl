<script lang="ts">
  /**
   * The sidebar layout editor (#169), shared by Instellingen → Navigatie (org default) and
   * the personal variant on the account screen. Reorders with arrow buttons (a list this
   * short doesn't earn drag-and-drop) and hides per item; one save button posts the ordered
   * `{key, hidden}` list as JSON (docs/UX.md: one save per editing surface).
   * Only module-contributed items appear — the fixed core items (Dashboard, Agenda,
   * Instellingen) are not anyone's to hide.
   *
   * In `renamable` mode (Instellingen → Navigatie only) each item — and each nav group — also
   * gets an optional tenant label via the shared `I18nTextField` (one field, NL/EN switcher,
   * never required; empty = the declared name, shown as the placeholder). Those labels are
   * org-wide config, so the personal editor never shows them; it still posts order + visibility
   * alone, and the row text there just reflects whatever the org renamed the item to.
   */
  import { ArrowDown, ArrowUp, Eye, EyeOff } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import type { NavLabelMap, NavPrefItem } from "$lib/core/registry";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import I18nTextField from "$lib/core/ui/I18nTextField.svelte";

  let {
    candidates,
    initial = null,
    action,
    resetAction = null,
    showReset = false,
    renamable = false,
    groups = [],
    initialGroups = [],
  }: {
    /** Every module nav item this level may arrange, in declared order. */
    candidates: { key: string; label: string }[];
    /** The saved layout at this level; null = declared positions. */
    initial?: NavPrefItem[] | null;
    action: string;
    /** Optional "terug naar standaard" action (personal level only). */
    resetAction?: string | null;
    /** Only offer the reset when there is a personal row to drop. */
    showReset?: boolean;
    /** Offer the per-item / per-group rename fields (org default editor only). */
    renamable?: boolean;
    /** The distinct nav groups declared by enabled modules; `label` is the declared heading. */
    groups?: { key: string; label: string }[];
    /** The org's saved group labels, merged onto `groups` by key. */
    initialGroups?: { key: string; label?: NavLabelMap }[];
  } = $props();

  interface Row {
    key: string;
    label: string;
    hidden: boolean;
    /** The org's saved per-locale label for this item (renamable editor only). */
    custom: NavLabelMap;
  }

  function initialRows(): Row[] {
    const byKey = new Map(candidates.map((c) => [c.key, c]));
    const rows: Row[] = [];
    for (const item of initial ?? []) {
      const candidate = byKey.get(item.key);
      if (!candidate) continue; // a module since disabled — drop quietly
      rows.push({
        key: item.key,
        label: candidate.label,
        hidden: Boolean(item.hidden),
        custom: item.label ?? null,
      });
      byKey.delete(item.key);
    }
    // Items the saved layout predates (a module enabled later) append in declared order.
    for (const candidate of byKey.values()) {
      rows.push({ key: candidate.key, label: candidate.label, hidden: false, custom: null });
    }
    return rows;
  }

  // svelte-ignore state_referenced_locally — an editor seeds from its load, deliberately.
  let rows = $state<Row[]>(initialRows());

  // One form, two submits (save / reset): the clicked one spins, both freeze (#279).
  const busy = new InFlight();
  let submitAction = $state<"save" | "reset">("save");
  const serialized = $derived(
    JSON.stringify(rows.map((row) => ({ key: row.key, hidden: row.hidden }))),
  );
  // The group keys ride in their own hidden field; their labels post as I18nTextField inputs.
  const groupKeys = $derived(JSON.stringify(groups.map((g) => g.key)));
  // svelte-ignore state_referenced_locally — the editor seeds its group labels once, deliberately.
  const groupLabelByKey = new Map(initialGroups.map((g) => [g.key, g.label ?? null]));

  function move(index: number, delta: number) {
    const next = index + delta;
    if (next < 0 || next >= rows.length) return;
    const copy = [...rows];
    [copy[index], copy[next]] = [copy[next], copy[index]];
    rows = copy;
  }
</script>

<form
  method="POST"
  {action}
  use:enhance={busy.wrap("", (input) => {
    submitAction = input.submitter?.hasAttribute("formaction") ? "reset" : "save";
  })}
  class="max-w-lg rounded-xl border border-border bg-surface-raised p-5"
>
  <input type="hidden" name="items" value={serialized} />
  {#if renamable}<input type="hidden" name="groups" value={groupKeys} />{/if}
  {#if rows.length === 0}
    <p class="text-sm text-text-muted">{t("settings.navigation.empty")}</p>
  {:else}
    <ul class="space-y-1">
      {#each rows as row, index (row.key)}
        <li class="rounded-lg border border-border px-3 py-2 {row.hidden ? 'opacity-50' : ''}">
          <div class="flex items-center gap-2">
            <span class="min-w-0 flex-1 truncate text-sm text-text">{row.label}</span>
            <button
              type="button"
              class="rounded p-1 text-text-muted hover:text-brand disabled:opacity-30"
              onclick={() => move(index, -1)}
              disabled={index === 0}
              aria-label={t("settings.navigation.move_up")}
            >
              <ArrowUp size={15} />
            </button>
            <button
              type="button"
              class="rounded p-1 text-text-muted hover:text-brand disabled:opacity-30"
              onclick={() => move(index, 1)}
              disabled={index === rows.length - 1}
              aria-label={t("settings.navigation.move_down")}
            >
              <ArrowDown size={15} />
            </button>
            <button
              type="button"
              class="rounded p-1 text-text-muted hover:text-brand"
              onclick={() => (rows[index] = { ...row, hidden: !row.hidden })}
              aria-label={row.hidden
                ? t("settings.navigation.show_item")
                : t("settings.navigation.hide_item")}
              aria-pressed={row.hidden}
            >
              {#if row.hidden}<EyeOff size={15} />{:else}<Eye size={15} />{/if}
            </button>
          </div>
          {#if renamable}
            <div class="mt-2">
              <I18nTextField
                label={t("settings.navigation.rename_label")}
                basename={`itemlabel_${row.key}`}
                values={row.custom ?? {}}
                placeholder={row.label}
                hint={false}
              />
            </div>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}

  {#if renamable && groups.length > 0}
    <div class="mt-6">
      <h3 class="text-sm font-semibold text-text">{t("settings.navigation.groups_title")}</h3>
      <p class="mb-2 mt-1 text-xs text-text-muted">{t("settings.navigation.groups_subtitle")}</p>
      <ul class="space-y-1">
        {#each groups as group (group.key)}
          <li class="rounded-lg border border-border px-3 py-2">
            <span class="block text-sm text-text">{group.label}</span>
            <div class="mt-2">
              <I18nTextField
                label={t("settings.navigation.rename_label")}
                basename={`grouplabel_${group.key}`}
                values={groupLabelByKey.get(group.key) ?? {}}
                placeholder={group.label}
                hint={false}
              />
            </div>
          </li>
        {/each}
      </ul>
    </div>
  {/if}
  <div class="mt-4 flex items-center justify-end gap-2">
    {#if resetAction && showReset}
      <Button
        type="submit"
        formaction={resetAction}
        variant="secondary"
        loading={busy.active && submitAction === "reset"}
        disabled={busy.active}
      >
        {t("settings.navigation.reset")}
      </Button>
    {/if}
    <Button loading={busy.active && submitAction === "save"} disabled={busy.active}>
      {t("common.save")}
    </Button>
  </div>
</form>
