<script lang="ts">
  /** My Day widget (#156): the viewer's own matched emails awaiting review — count + the
   *  next few, each naming its sender and client, linking into the review queue. */
  import { t } from "$lib/core/i18n";

  let { data }: { data: unknown } = $props();

  interface PendingRow {
    id: string;
    subject: string | null;
    company_name?: string | null;
    participants?: { email: string; name?: string | null; role?: string }[];
  }
  const payload = $derived((data ?? { items: [], total: 0 }) as { items: PendingRow[]; total: number });

  const sender = (row: PendingRow) => {
    const from = row.participants?.find((p) => p.role === "from");
    return from?.name || from?.email || "";
  };
</script>

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
  <a href="/interactions/review" class="mt-2 inline-block text-xs font-medium text-brand hover:underline">
    {t("interactions.widget.review_all", { count: payload.total })}
  </a>
{/if}
