<script lang="ts" module>
  import type { Component } from "svelte";

  /** One entry in the ⋯ menu: a link (href) or an action (onclick), optionally destructive. */
  export type ActionItem = {
    label: string;
    icon?: Component;
    href?: string;
    onclick?: () => void;
    danger?: boolean;
    disabled?: boolean;
  };
</script>

<script lang="ts">
  /**
   * The single overflow (⋯) menu used everywhere record-level Edit/Delete actions live
   * (docs/UX.md). Destructive and edit-definition actions are kept out of the row/header so
   * they can't be clicked by accident — they open from here, and deletes always confirm.
   */
  import { EllipsisVertical } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";

  let {
    items,
    label,
    align = "right",
    size,
    compact = false,
  }: {
    items: ActionItem[];
    /** aria-label for the trigger; defaults to the shared "Actions" string. */
    label?: string;
    align?: "right" | "left";
    size?: number;
    /** Borderless, smaller trigger for inline rows (comments, checklist items, links). */
    compact?: boolean;
  } = $props();

  let open = $state(false);
  let root: HTMLElement | undefined = $state();

  const iconSize = $derived(size ?? (compact ? 15 : 16));
  const triggerClass = $derived(
    compact
      ? "rounded p-1 text-text-muted hover:text-text"
      : "rounded-lg border border-border p-2 text-text-muted hover:border-brand hover:text-brand",
  );

  function onWindowClick(e: MouseEvent) {
    if (open && root && !root.contains(e.target as Node)) open = false;
  }
  function select(item: ActionItem) {
    if (item.disabled) return;
    open = false;
    item.onclick?.();
  }
</script>

<svelte:window
  onclick={onWindowClick}
  onkeydown={(e) => {
    if (e.key === "Escape") open = false;
  }}
/>

<div class="relative shrink-0" bind:this={root}>
  <button
    type="button"
    class={triggerClass}
    onclick={() => (open = !open)}
    aria-haspopup="menu"
    aria-expanded={open}
    aria-label={label ?? t("common.actions")}
  >
    <EllipsisVertical size={iconSize} />
  </button>

  {#if open}
    <div
      role="menu"
      class="absolute z-30 mt-1 w-48 rounded-xl border border-border bg-surface-raised py-1 shadow-lg
        {align === 'right' ? 'right-0' : 'left-0'}"
    >
      {#each items as item (item.label)}
        {@const Icon = item.icon}
        {#if item.href && !item.disabled}
          <a
            role="menuitem"
            href={item.href}
            onclick={() => (open = false)}
            class="flex items-center gap-2 px-4 py-2 text-sm hover:bg-surface
              {item.danger
              ? 'text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950'
              : 'text-text'}"
          >
            {#if Icon}<Icon size={15} class={item.danger ? "" : "text-text-muted"} />{/if}
            {item.label}
          </a>
        {:else}
          <button
            type="button"
            role="menuitem"
            disabled={item.disabled}
            onclick={() => select(item)}
            class="flex w-full items-center gap-2 px-4 py-2 text-left text-sm hover:bg-surface disabled:cursor-not-allowed disabled:opacity-50
              {item.danger
              ? 'text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950'
              : 'text-text'}"
          >
            {#if Icon}<Icon size={15} class={item.danger ? "" : "text-text-muted"} />{/if}
            {item.label}
          </button>
        {/if}
      {/each}
    </div>
  {/if}
</div>
