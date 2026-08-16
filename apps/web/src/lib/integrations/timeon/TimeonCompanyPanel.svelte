<script lang="ts">
  /**
   * "Is this client's time registration in step?" — the timeon panel on a company detail page.
   *
   * The API contributes this as a server `PanelSpec` (`app/integrations/timeon/panels.py`), so
   * what arrives here is the dict that provider returned; this file is only the drawing. Without
   * it the company hub falls back to a raw `<pre>{JSON}</pre>`, which is why an API panel still
   * needs a web counterpart even when it contributes no screen of its own.
   *
   * It reads stored pairings and never calls Timeon — the provider says so and this side must not
   * quietly undo it by fetching something. A company page is opened all day; a timesheet API is
   * not on that path.
   *
   * Read-only. Settling a conflict and running a sync are acts on the *connection*, they are
   * gated on another permission, and they live on the sync workspace. What belongs here is the
   * answer, plus the one link that takes somebody to where they can do something about it.
   */
  import { AlertTriangle, RefreshCw } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";

  let { data }: { data: Record<string, unknown> } = $props();

  const linked = $derived(Boolean(data.linked));
  const customerName = $derived((data.customer_name as string | null) ?? null);
  const customerId = $derived((data.customer_id as string | null) ?? null);
  const organisation = $derived((data.organisation as string | null) ?? null);
  const hours = $derived((data.hours ?? {}) as Record<string, number>);
  const paired = $derived(Number(hours.linked ?? 0));
  const drift = $derived(Number(hours.drift ?? 0) + Number(hours.missing ?? 0));
  const failed = $derived(Number(hours.error ?? 0));
  const conflicts = $derived(Number(data.open_conflicts ?? 0));
</script>

{#if !linked}
  <p class="text-sm text-text-muted">{t("timeon.panel.unlinked")}</p>
{:else}
  <div class="flex items-start gap-2">
    <RefreshCw size={16} class="mt-0.5 shrink-0 text-text-muted" aria-hidden="true" />
    <div class="min-w-0 flex-1">
      <p class="truncate text-sm font-medium text-text">
        {customerName ?? t("timeon.panel.customer_unnamed")}
        {#if customerId}
          <span class="font-normal text-text-muted">({customerId})</span>
        {/if}
      </p>
      <p class="mt-0.5 text-xs text-text-muted">
        {organisation ?? t("timeon.panel.no_organisation")}
      </p>
    </div>
  </div>

  <!-- Three numbers, and the two that need a person carry a colour. A conflict is not a fault:
       it is two people having both been right, and it is the only one with a queue. -->
  <dl class="mt-3 grid grid-cols-3 gap-2 text-sm">
    <div>
      <dt class="text-xs text-text-muted">{t("timeon.panel.hours_paired")}</dt>
      <dd class="text-text">{paired}</dd>
    </div>
    <div>
      <dt class="text-xs text-text-muted">{t("timeon.panel.hours_drift")}</dt>
      <dd class={drift > 0 ? "text-amber-600" : "text-text"}>{drift}</dd>
    </div>
    <div>
      <dt class="text-xs text-text-muted">{t("timeon.panel.conflicts")}</dt>
      <dd class={conflicts > 0 ? "text-amber-600" : "text-text"}>{conflicts}</dd>
    </div>
  </dl>

  {#if failed > 0}
    <p class="mt-3 flex items-start gap-1.5 text-xs text-red-600">
      <AlertTriangle size={14} class="mt-0.5 shrink-0" aria-hidden="true" />
      <span>{t("timeon.panel.failed", { count: failed })}</span>
    </p>
  {/if}

  <a class="mt-3 inline-block text-sm text-brand hover:underline" href="/timeon">
    {t("timeon.panel.open_workspace")}
  </a>
{/if}
