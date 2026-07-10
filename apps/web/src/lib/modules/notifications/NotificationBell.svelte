<script lang="ts">
  /**
   * The header bell (issue #16).
   *
   * The header is deliberately minimal (docs/UX.md), so this is an icon and a count and nothing
   * else. The count is the API's — never `items.length`, which would count the page we happened
   * to load rather than the unread total.
   *
   * Liveness needs a new convention: layout loads do not rerun on navigation, so the SSR count
   * would go stale the moment you clicked anything. The bell therefore polls a light proxy
   * endpoint once a minute. That is a deliberate precedent, not an accident — there is no
   * SSE/WebSocket infrastructure here, and a badge that lies is worse than one that lags.
   */
  import { invalidateAll } from "$app/navigation";
  import { Bell, CheckCheck } from "@lucide/svelte";

  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";

  import { notificationHref, notificationText, type NotificationLike } from "./format";

  interface BellItem extends NotificationLike {
    id: string;
    actor_name: string | null;
    created_at: string;
  }

  /** The unread total from the layout's SSR load; the poll takes over once it has an answer. */
  let { count = 0 }: { count?: number } = $props();

  let open = $state(false);
  /** Non-null once the browser has its own answer. Keeps `count` as a pure prop (no re-sync). */
  let polled = $state<{ count: number; items: BellItem[] } | null>(null);

  const unread = $derived(polled?.count ?? count);
  const items = $derived(polled?.items ?? []);

  async function refresh(): Promise<void> {
    const response = await fetch("/notifications/bell");
    if (response.ok) polled = await response.json();
  }

  async function markAllRead(): Promise<void> {
    const response = await fetch("/notifications/bell", { method: "POST" });
    if (response.ok) polled = await response.json();
    // The /notifications page, if that is where we are, must not keep showing them unread.
    await invalidateAll();
  }

  $effect(() => {
    const timer = setInterval(() => void refresh(), 60_000);
    return () => clearInterval(timer);
  });

  function toggle(): void {
    open = !open;
    // The badge knows the count; the popover needs the rows behind it.
    if (open && polled === null) void refresh();
  }
</script>

<svelte:window
  onclick={(e) => {
    if (open && !(e.target as HTMLElement).closest?.("[data-notification-menu]")) open = false;
  }}
/>

<div class="relative" data-notification-menu>
  <button
    type="button"
    class="relative rounded-full p-2 text-text-muted hover:bg-surface hover:text-text"
    onclick={toggle}
    aria-haspopup="menu"
    aria-expanded={open}
    aria-label={unread > 0
      ? t("notifications.bell.aria_unread", { count: unread })
      : t("notifications.bell.aria")}
  >
    <Bell size={20} />
    {#if unread > 0}
      <span
        class="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-600 px-1 text-[10px] font-semibold tabular-nums text-white"
      >
        {unread > 99 ? "99+" : unread}
      </span>
    {/if}
  </button>

  {#if open}
    <div
      role="menu"
      class="absolute right-0 z-30 mt-1 w-[22rem] max-w-[calc(100vw-2rem)] rounded-xl border border-border bg-surface-raised py-1 shadow-lg"
    >
      <div class="flex items-center justify-between gap-2 border-b border-border px-4 py-2.5">
        <p class="text-sm font-semibold text-text">{t("notifications.bell.title")}</p>
        {#if unread > 0}
          <button
            type="button"
            class="flex items-center gap-1.5 rounded px-1.5 py-0.5 text-xs text-text-muted hover:text-brand"
            onclick={markAllRead}
          >
            <CheckCheck size={14} />
            {t("notifications.bell.mark_all_read")}
          </button>
        {/if}
      </div>

      {#if items.length === 0}
        <p class="px-4 py-6 text-center text-sm text-text-muted">
          {t("notifications.bell.empty")}
        </p>
      {:else}
        <ul class="max-h-96 overflow-y-auto" data-sveltekit-preload-data="hover">
          {#each items as item (item.id)}
            {@const href = notificationHref(item)}
            <li class="border-b border-border last:border-0">
              <svelte:element
                this={href ? "a" : "div"}
                {href}
                class="block px-4 py-2.5 hover:bg-surface"
                onclick={() => (open = false)}
                role={href ? undefined : "presentation"}
              >
                <p class="text-sm text-text">
                  <!-- A person's event is a predicate after their name; a system reminder is
                       already a whole sentence and names no actor. -->
                  {#if item.actor_name}
                    <span class="font-medium">{item.actor_name}</span>
                    {notificationText(item)}
                  {:else}
                    {notificationText(item)}
                  {/if}
                </p>
                <p class="mt-0.5 text-xs text-text-muted">{fmtDateTime(item.created_at)}</p>
              </svelte:element>
            </li>
          {/each}
        </ul>
      {/if}

      <a
        href="/notifications"
        class="block border-t border-border px-4 py-2 text-center text-sm text-brand hover:bg-surface"
        onclick={() => (open = false)}
      >
        {t("notifications.bell.view_all")}
      </a>
    </div>
  {/if}
</div>
