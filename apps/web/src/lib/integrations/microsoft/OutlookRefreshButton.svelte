<script lang="ts">
  /**
   * "Scan mailbox now" for an Outlook mailbox, plus when it last happened — the Gmail button's
   * twin (#341), drawn beside it for a viewer who holds both.
   *
   * The Outlook feed is a five-minute cron, which is invisible: an email sent thirty seconds ago
   * is simply not on the timeline yet, and nothing on the screen said whether that meant "not
   * synced yet" or "not matched at all". So the timeline states its own freshness — an absolute
   * timestamp, in the tenant's zone — and a request to make it fresher, rate-limited by the API to
   * one manual poll per minute per mailbox. A refused press is a countdown, never an error line.
   *
   * It draws nothing unless this user's own mailbox is actually opted in and working: a control
   * that always refuses is a broken control (#253).
   */
  import { RefreshCw } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import type { components } from "$lib/core/api/schema";
  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";

  type Status = components["schemas"]["OutlookSyncStatus"];
  type Result = components["schemas"]["OutlookRefreshResult"];

  let {
    status = null,
    result = null,
  }: {
    /** The section layout's `/microsoft/outlook/status` read — `null` when it was never asked. */
    status?: Status | null;
    /** This page's last `refreshOutlook` action result, if the user just pressed it. */
    result?: Result | null;
  } = $props();

  const busy = new InFlight();

  /** The freshest status we hold: the action's answer outranks the load's. */
  const sync = $derived(result?.sync ?? status);

  // The cooldown as a deadline rather than a counter, so the ticker never depends on its own
  // write (an $effect that reads what it sets re-runs itself forever).
  let deadline = $state(0);
  let now = $state(0);
  $effect(() => {
    const seconds = sync?.retry_after_seconds ?? 0;
    now = Date.now();
    deadline = seconds > 0 ? now + seconds * 1000 : 0;
  });
  $effect(() => {
    if (deadline <= 0) return;
    const id = setInterval(() => {
      now = Date.now();
      if (now >= deadline) clearInterval(id);
    }, 500);
    return () => clearInterval(id);
  });
  const remaining = $derived(deadline > 0 ? Math.max(0, Math.ceil((deadline - now) / 1000)) : 0);

  const outcome = $derived.by(() => {
    if (!result) return null;
    if (result.status === "error") return t("microsoft.outlook.refresh_failed");
    if (result.status === "cooldown") return null; // the countdown already says it
    if (result.logged === 0) return t("microsoft.outlook.refreshed_none");
    return result.logged === 1
      ? t("microsoft.outlook.refreshed_one")
      : t("microsoft.outlook.refreshed_other", { count: result.logged });
  });
</script>

{#if sync?.available}
  <div class="flex flex-col items-end gap-0.5">
    <form method="POST" action="?/refreshOutlook" use:enhance={busy.keep("refresh-outlook")}>
      <Button
        type="submit"
        variant="secondary"
        loading={busy.is("refresh-outlook")}
        disabled={remaining > 0}
        title={t("microsoft.outlook.refresh_hint")}
      >
        {#if !busy.is("refresh-outlook")}
          <RefreshCw size={15} aria-hidden="true" />
        {/if}
        {t("microsoft.outlook.refresh")}
      </Button>
    </form>
    <p class="text-xs text-text-muted">
      {#if remaining > 0}
        {t("microsoft.outlook.cooldown", { seconds: remaining })}
      {:else if outcome}
        {outcome}
      {:else if sync.last_polled_at}
        {t("microsoft.outlook.last_refresh", { when: fmtDateTime(sync.last_polled_at) })}
      {:else}
        {t("microsoft.outlook.never_refreshed")}
      {/if}
    </p>
  </div>
{/if}
