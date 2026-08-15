<script lang="ts">
  /**
   * "Scan mailbox now", plus when it last happened (#341).
   *
   * The Gmail feed is a five-minute cron, which is invisible: an email you sent thirty seconds
   * ago is simply not on the timeline yet, and nothing on the screen said whether that meant
   * "not synced yet" or "not matched at all". So the timeline now states its own freshness —
   * an absolute timestamp, in the tenant's zone like every other one, rather than a "2 minuten
   * geleden" that keeps ticking and can never be checked against anything.
   *
   * The button is a *request*, not a guarantee: the API rate-limits it to one manual poll per
   * minute per mailbox (`app/integrations/google/gmail/refresh.py`), because a scan costs Gmail API
   * quota and the cron is already covering the mailbox anyway. A refused press is not an error
   * line — the countdown below the button says how long, and the last-scan time stays visible.
   *
   * It draws nothing at all unless this user's own mailbox is actually opted in and working:
   * a control that always refuses is a broken control (#253), and "connect your mailbox" is
   * Instellingen → Account's job, not this list's.
   */
  import { RefreshCw } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import type { components } from "$lib/core/api/schema";
  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";

  type Status = components["schemas"]["GmailSyncStatus"];
  type Result = components["schemas"]["GmailRefreshResult"];

  let {
    status = null,
    result = null,
  }: {
    /** The section layout's `/google/gmail/status` read — `null` when it was never asked. */
    status?: Status | null;
    /** This page's last `refreshGmail` action result, if the user just pressed it. */
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
    if (result.status === "error") return t("google.gmail.refresh_failed");
    if (result.status === "cooldown") return null; // the countdown already says it
    if (result.logged === 0) return t("google.gmail.refreshed_none");
    return result.logged === 1
      ? t("google.gmail.refreshed_one")
      : t("google.gmail.refreshed_other", { count: result.logged });
  });
</script>

{#if sync?.available}
  <div class="flex flex-col items-end gap-0.5">
    <form method="POST" action="?/refreshGmail" use:enhance={busy.keep("refresh-gmail")}>
      <Button
        type="submit"
        variant="secondary"
        loading={busy.is("refresh-gmail")}
        disabled={remaining > 0}
        title={t("google.gmail.refresh_hint")}
      >
        {#if !busy.is("refresh-gmail")}
          <RefreshCw size={15} aria-hidden="true" />
        {/if}
        {t("google.gmail.refresh")}
      </Button>
    </form>
    <p class="text-xs text-text-muted">
      {#if remaining > 0}
        {t("google.gmail.cooldown", { seconds: remaining })}
      {:else if outcome}
        {outcome}
      {:else if sync.last_polled_at}
        {t("google.gmail.last_refresh", { when: fmtDateTime(sync.last_polled_at) })}
      {:else}
        {t("google.gmail.never_refreshed")}
      {/if}
    </p>
  </div>
{/if}
