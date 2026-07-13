<script lang="ts">
  /** My Day widget (#156): the viewer's own matched emails awaiting review — count + the
   *  next few, each naming its sender and client, linking into the review queue.
   *
   *  Mail keeps arriving while the dashboard sits open, and the page's SSR load never reruns
   *  on its own — so the widget polls its own list once a minute, the same deliberate
   *  precedent as the notification bell (#171): no SSE/WebSocket infrastructure here. */
  import { untrack } from "svelte";

  import { t } from "$lib/core/i18n";
  import DashboardWidgetCard from "$lib/core/ui/DashboardWidgetCard.svelte";

  let { data }: { data: unknown } = $props();

  interface PendingRow {
    id: string;
    subject: string | null;
    company_name?: string | null;
    participants?: { email: string; name?: string | null; role?: string }[];
  }
  interface Payload {
    items: PendingRow[];
    total: number;
  }

  /** The browser's own answer; a rerun SSR load (data changed) retires it, server wins. */
  let polled = $state<Payload | null>(null);
  $effect(() => {
    void data;
    polled = null;
  });
  const payload = $derived(polled ?? ((data ?? { items: [], total: 0 }) as Payload));

  async function refresh(): Promise<void> {
    const response = await fetch("/api/v1/interactions?status=pending&mine=true&limit=5", {
      headers: { accept: "application/json" },
    });
    if (!response.ok) return;
    const body = await response.json();
    polled = { items: body.items ?? [], total: body.total ?? 0 };
  }

  $effect(() => {
    // `untrack` keeps the timer from being torn down when `polled` changes underneath.
    const timer = setInterval(() => untrack(() => void refresh()), 60_000);
    return () => clearInterval(timer);
  });

  const sender = (row: PendingRow) => {
    const from = row.participants?.find((p) => p.role === "from");
    return from?.name || from?.email || "";
  };
</script>

<DashboardWidgetCard
  title={t("dashboard.widget.interactions.pending_email")}
  href={payload.total > 0 ? "/interactions/review" : undefined}
  linkLabel={t("interactions.widget.review_all", { count: payload.total })}
>
  {#if payload.total === 0}
    <p class="text-sm text-text-muted">{t("interactions.widget.empty")}</p>
  {:else}
    <ul class="divide-y divide-border">
      {#each payload.items as row (row.id)}
        <li class="py-1.5">
          <a href="/interactions/review" class="block min-w-0 hover:text-brand">
            <span class="block truncate text-sm text-text">{row.subject || "—"}</span>
            <span class="block truncate text-xs text-text-muted">
              {sender(row)}{#if row.company_name}&nbsp;· {row.company_name}{/if}
            </span>
          </a>
        </li>
      {/each}
    </ul>
  {/if}
</DashboardWidgetCard>
