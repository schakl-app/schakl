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
  import { untrack } from "svelte";

  import { invalidateAll } from "$app/navigation";
  import { Bell, CheckCheck, Settings } from "@lucide/svelte";

  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";

  import { notificationHref, notificationText, type NotificationLike } from "./format";

  interface BellItem extends NotificationLike {
    id: string;
    actor_name: string | null;
    created_at: string;
  }

  /** The unread total from the layout's SSR load; the poll refines it between loads. */
  let { count = 0 }: { count?: number } = $props();

  let open = $state(false);
  /**
   * The browser's own answer, stamped with the `count` it was fetched against.
   *
   * That stamp is the whole point. Marking something read anywhere reruns the layout load and
   * hands us a new `count`; a cache that simply won over the prop would then keep showing the
   * *old* total until the next tick — a badge that lies for up to a minute. So a cache whose
   * stamp no longer matches the server's count is stale by definition, and the server wins.
   */
  let polled = $state<{ count: number; items: BellItem[]; basedOn: number } | null>(null);

  const fresh = $derived(polled !== null && polled.basedOn === count ? polled : null);
  const unread = $derived(fresh?.count ?? count);
  const items = $derived(fresh?.items ?? []);
  /** Distinguishes "nothing to show" from "not asked yet" — an empty state must not flash. */
  const loading = $derived(fresh === null);

  async function refresh(): Promise<void> {
    const basedOn = count;
    const response = await fetch("/notifications/bell");
    if (response.ok) polled = { ...(await response.json()), basedOn };
  }

  async function markAllRead(): Promise<void> {
    await fetch("/notifications/bell", { method: "POST" });
    // The /notifications page, if that is where we are, must not keep showing them unread.
    // The reload also hands the bell a fresh `count`, which retires the cache above.
    await invalidateAll();
  }

  /**
   * Clicking a popover row marks *that* one read (issue #164) — the bell only ever lists unread
   * items, so a click always decrements. Optimistic first so the badge and list update now, not
   * on the next 60s poll: reassign a new `polled` (Svelte 5 reactivity) with the same `basedOn`
   * stamp so `fresh` still derives from the decremented cache. The PATCH is fire-and-forget so a
   * linked row's navigation is never blocked; the next refresh() reconciles against the server.
   * Runs for both branches — a linkless system item now dismisses+marks read instead of no-op.
   */
  function markRead(item: BellItem): void {
    if (polled) {
      polled = {
        basedOn: polled.basedOn,
        count: Math.max(0, polled.count - 1),
        items: polled.items.filter((i) => i.id !== item.id),
      };
    }
    void fetch("/notifications/bell", {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ id: item.id }),
    });
  }

  $effect(() => {
    // Fetch once on mount so the popover has rows before it is ever opened, then keep the badge
    // honest between navigations. `untrack` keeps the timer from being torn down on every count.
    untrack(() => void refresh());
    const timer = setInterval(() => void refresh(), 60_000);
    return () => clearInterval(timer);
  });

  function toggle(): void {
    open = !open;
    // The badge knows the count; the popover needs the rows behind it.
    if (open && loading) void refresh();
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
    <!-- On a phone the bell isn't flush-right (the avatar sits to its right), so an
         `absolute right-0` panel anchored to the bell computes a negative left edge and runs
         off the *left* of the screen once it has rows in it (issue #73). Pin it to the viewport
         instead — `fixed inset-x-2` below the header — and only anchor it to the bell on `sm+`. -->
    <div
      role="menu"
      class="fixed inset-x-2 top-14 z-30 rounded-xl border border-border bg-surface-raised py-1 shadow-lg sm:absolute sm:inset-x-auto sm:right-0 sm:top-auto sm:mt-1 sm:w-[22rem]"
    >
      <div class="flex items-center justify-between gap-2 border-b border-border px-4 py-2.5">
        <p class="text-sm font-semibold text-text">{t("notifications.bell.title")}</p>
        <div class="flex items-center gap-1">
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
          <!-- Personal notification preferences (issue #163). Ungated by settings permissions,
               same visibility as the bell itself — the only nav route to /settings/notifications
               a member without settings.* has, now the profile-menu entry is gone. -->
          <a
            href="/settings/notifications"
            class="rounded p-1 text-text-muted hover:text-brand"
            title={t("notifications.bell.settings")}
            aria-label={t("notifications.bell.settings")}
            onclick={() => (open = false)}
          >
            <Settings size={16} />
          </a>
        </div>
      </div>

      {#if loading}
        <p class="px-4 py-6 text-center text-sm text-text-muted">{t("common.loading")}</p>
      {:else if items.length === 0}
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
                class="block cursor-pointer px-4 py-2.5 hover:bg-surface"
                onclick={() => {
                  markRead(item);
                  open = false;
                }}
                role={href ? undefined : "presentation"}
              >
                <p class="break-words text-sm text-text">
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
