<script lang="ts">
  /**
   * A record's activity, rendered (issue #16).
   *
   * The presentational half of both panels — the client's (an API panel provider hands it an
   * opaque dict) and the project's (a typed `EntityPanelSpec` load). Only the plumbing differs,
   * so only the plumbing is duplicated.
   *
   * Recipient-independent by design: this is what happened to the record, not what was sent to
   * you. If it truncates, it says so — silent truncation reads as "that's all of them"
   * (docs/PERFORMANCE.md).
   */
  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";

  import { notificationText, type NotificationLike } from "./format";

  interface ActivityItem extends NotificationLike {
    id: string;
    actor_name: string | null;
    created_at: string;
  }

  let { items, limit }: { items: ActivityItem[]; limit: number } = $props();
</script>

{#if items.length === 0}
  <p class="text-sm text-text-muted">{t("notifications.activity.empty")}</p>
{:else}
  <ol class="space-y-3">
    {#each items as item (item.id)}
      <li class="flex gap-3 text-sm">
        <span class="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-border" aria-hidden="true"></span>
        <span class="min-w-0 flex-1">
          <span class="text-text">
            <!-- A person's event is a predicate after their name; a system event stands alone. -->
            {#if item.actor_name}
              <span class="font-medium">{item.actor_name}</span>
              {notificationText(item)}
            {:else}
              {notificationText(item)}
            {/if}
          </span>
          <span class="mt-0.5 block text-xs text-text-muted">{fmtDateTime(item.created_at)}</span>
        </span>
      </li>
    {/each}
  </ol>
  {#if items.length >= limit}
    <p class="mt-3 text-xs text-text-muted">
      {t("notifications.activity.truncated", { limit })}
    </p>
  {/if}
{/if}
