<script lang="ts">
  /**
   * The permission matrix, grouped by module (issue #19).
   *
   * **A grid is not a mobile UI** (docs/UX.md). There is no twelve-column table here even on a
   * desktop: each module is a `<details>` accordion of `label … control` rows, which stacks on a
   * phone by construction and never scrolls sideways.
   *
   * Two controls, because there are two kinds of permission:
   *  - unscoped → one checkbox;
   *  - scoped (`:own` / `:any`) → a three-way choice, because "may edit their own hours" and "may
   *    edit anyone's" are different grants and a checkbox cannot say which.
   *
   * The selection is **state**, not DOM. A radio rendered with a one-way `checked={…}` loses its
   * mark on hydration, and "select all in this module" would then have to reach into the DOM and
   * fight whatever Svelte does next. `bind:group` / `bind:checked` makes both problems go away.
   *
   * Native checkboxes and radios inherit the huisstijl via `accent-color` (docs/UX.md); nothing
   * here restyles them. There is **one save button for the whole set**, owned by the parent form
   * (docs/UX.md: one save per editing surface — per-field saves are a corrected mistake).
   */
  import { untrack } from "svelte";

  import { t } from "$lib/core/i18n";

  import {
    currentScope,
    groupPermissions,
    holdsUnscoped,
    scopeField,
    type PermissionCatalog,
    type Scope,
  } from "./permissions";

  let {
    catalog,
    granted,
    disabled = false,
    formId,
  }: {
    catalog: PermissionCatalog;
    /** The role's stored permission strings — scoped ones carry their suffix. */
    granted: string[];
    /** The owner role: every permission, always, and not editable. */
    disabled?: boolean;
    /** So the controls can live outside the `<form>` element itself. */
    formId?: string;
  } = $props();

  const groups = $derived(groupPermissions(catalog));

  const SCOPES: { value: Scope; labelKey: string }[] = [
    { value: "", labelKey: "settings.roles.scope_off" },
    { value: "own", labelKey: "settings.roles.scope_own" },
    { value: "any", labelKey: "settings.roles.scope_any" },
  ];

  // Seeded from the role exactly once — a different role is a different page load, so there is
  // nothing to re-sync, and `untrack` says so rather than leaving a reactivity warning behind.
  const scoped = $state<Record<string, Scope>>(
    untrack(() =>
      Object.fromEntries(
        catalog.permissions
          .filter((p) => p.scopes.length > 0)
          .map((p) => [p.key, currentScope(granted, p.key)]),
      ),
    ),
  );
  const unscoped = $state<Record<string, boolean>>(
    untrack(() =>
      Object.fromEntries(
        catalog.permissions
          .filter((p) => p.scopes.length === 0)
          .map((p) => [p.key, holdsUnscoped(granted, p.key)]),
      ),
    ),
  );

  /** Tick every permission in a module, or clear it. Scoped ones go to the broadest scope. */
  function setGroup(group: string, on: boolean) {
    for (const permission of catalog.permissions) {
      if (permission.group !== group) continue;
      if (permission.scopes.length > 0) scoped[permission.key] = on ? "any" : "";
      else unscoped[permission.key] = on;
    }
  }
</script>

<div class="space-y-3">
  {#each groups as { group, permissions } (group)}
    <details open class="rounded-xl border border-border bg-surface-raised">
      <summary
        class="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-sm font-semibold text-text"
      >
        <span>{t(`permissions.group.${group}`)}</span>
        {#if !disabled}
          <span class="flex items-center gap-2 text-xs font-normal">
            <button
              type="button"
              class="rounded-md px-2 py-1 text-text-muted hover:bg-surface hover:text-text"
              onclick={() => setGroup(group, true)}
            >
              {t("settings.roles.select_all")}
            </button>
            <button
              type="button"
              class="rounded-md px-2 py-1 text-text-muted hover:bg-surface hover:text-text"
              onclick={() => setGroup(group, false)}
            >
              {t("settings.roles.clear_all")}
            </button>
          </span>
        {/if}
      </summary>

      <ul class="divide-y divide-border border-t border-border">
        {#each permissions as permission (permission.key)}
          <li class="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
            <span class="text-sm text-text">{t(permission.label_key)}</span>

            {#if permission.scopes.length > 0}
              <fieldset class="flex flex-wrap items-center gap-3" {disabled}>
                <legend class="sr-only">{t(permission.label_key)}</legend>
                {#each SCOPES as option (option.value)}
                  <label class="flex items-center gap-1.5 text-sm text-text-muted">
                    <input
                      type="radio"
                      form={formId}
                      name={scopeField(permission.key)}
                      value={option.value}
                      bind:group={scoped[permission.key]}
                      class="h-4 w-4 border-border"
                    />
                    {t(option.labelKey)}
                  </label>
                {/each}
              </fieldset>
            {:else}
              <input
                type="checkbox"
                form={formId}
                name="permissions"
                value={permission.key}
                bind:checked={unscoped[permission.key]}
                {disabled}
                class="h-4 w-4 rounded border-border"
                aria-label={t(permission.label_key)}
              />
            {/if}
          </li>
        {/each}
      </ul>
    </details>
  {/each}
</div>
