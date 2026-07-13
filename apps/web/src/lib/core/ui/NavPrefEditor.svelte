<script lang="ts">
  /**
   * The sidebar layout editor (#169), shared by Instellingen → Navigatie (org default) and
   * the personal variant on the account screen. Reorders with arrow buttons (a list this
   * short doesn't earn drag-and-drop) and hides per item; one save button posts the ordered
   * `{key, hidden}` list as JSON (docs/UX.md: one save per editing surface).
   * Only module-contributed items appear — the fixed core items (Dashboard, Agenda,
   * Instellingen) are not anyone's to hide.
   */
  import { ArrowDown, ArrowUp, Eye, EyeOff } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import type { NavPrefItem } from "$lib/core/registry";

  let {
    candidates,
    initial = null,
    action,
    resetAction = null,
    showReset = false,
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
  } = $props();

  interface Row {
    key: string;
    label: string;
    hidden: boolean;
  }

  function initialRows(): Row[] {
    const byKey = new Map(candidates.map((c) => [c.key, c]));
    const rows: Row[] = [];
    for (const item of initial ?? []) {
      const candidate = byKey.get(item.key);
      if (!candidate) continue; // a module since disabled — drop quietly
      rows.push({ key: item.key, label: candidate.label, hidden: Boolean(item.hidden) });
      byKey.delete(item.key);
    }
    // Items the saved layout predates (a module enabled later) append in declared order.
    for (const candidate of byKey.values()) {
      rows.push({ key: candidate.key, label: candidate.label, hidden: false });
    }
    return rows;
  }

  // svelte-ignore state_referenced_locally — an editor seeds from its load, deliberately.
  let rows = $state<Row[]>(initialRows());
  const serialized = $derived(
    JSON.stringify(rows.map((row) => ({ key: row.key, hidden: row.hidden }))),
  );

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
  use:enhance
  class="max-w-lg rounded-xl border border-border bg-surface-raised p-5"
>
  <input type="hidden" name="items" value={serialized} />
  {#if rows.length === 0}
    <p class="text-sm text-text-muted">{t("settings.navigation.empty")}</p>
  {:else}
    <ul class="space-y-1">
      {#each rows as row, index (row.key)}
        <li
          class="flex items-center gap-2 rounded-lg border border-border px-3 py-2 {row.hidden
            ? 'opacity-50'
            : ''}"
        >
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
        </li>
      {/each}
    </ul>
  {/if}
  <div class="mt-4 flex items-center justify-end gap-2">
    {#if resetAction && showReset}
      <button
        type="submit"
        formaction={resetAction}
        class="rounded-lg border border-border px-4 py-2 text-sm text-text-muted hover:text-text"
      >
        {t("settings.navigation.reset")}
      </button>
    {/if}
    <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90">
      {t("common.save")}
    </button>
  </div>
</form>
