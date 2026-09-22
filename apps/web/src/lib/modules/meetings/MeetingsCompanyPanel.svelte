<script lang="ts">
  /**
   * The meetings panel on a client's page: the last few recorded meetings, newest first, each
   * a link to its minutes. Everything drawn came down with the page in the panel provider's
   * payload — no fetch of its own (docs/PERFORMANCE.md).
   */
  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import PanelRow from "$lib/core/ui/PanelRow.svelte";
  import PanelRows from "$lib/core/ui/PanelRows.svelte";

  import { fmtDuration, statusLabel, statusState } from "./format";
  import type { MeetingRow } from "./types";

  let { companyId, data }: { companyId: string; data: Record<string, unknown> } = $props();

  const items = $derived((data.items ?? []) as MeetingRow[]);
  const total = $derived((data.total ?? items.length) as number);
</script>

<PanelRows
  rows={items}
  {total}
  href={`/meetings?company=${companyId}`}
  linkLabel={t("meetings.panel.view_all", { count: String(total) })}
>
  {#snippet children(rows)}
    <ul class="divide-y divide-border">
      {#each rows as meeting (meeting.id)}
        <PanelRow
          href={`/meetings/${meeting.id}`}
          title={meeting.title}
          meta={[fmtDateTime(meeting.occurred_at), fmtDuration(meeting.duration_seconds)]
            .filter(Boolean)
            .join(" · ")}
          value={meeting.action_item_count
            ? t("meetings.panel.action_items", { count: String(meeting.action_item_count) })
            : null}
          chip={statusLabel(meeting.status)}
          chipState={statusState(meeting.status)}
        />
      {/each}
    </ul>
  {/snippet}
</PanelRows>
