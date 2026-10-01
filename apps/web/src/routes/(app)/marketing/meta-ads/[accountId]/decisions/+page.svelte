<script lang="ts">
  /**
   * The decisions log: every change made to this account from here, with the person and the
   * reason. The entry worth having is the one Meta cannot give — *why* a budget went from
   * € 30 to € 50 on a Tuesday, and that a campaign was looked at and deliberately left alone.
   */
  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import Card from "$lib/core/ui/Card.svelte";
  import Pagination from "$lib/core/ui/Pagination.svelte";
  import { decisionLabel, fmtCents, subjectLabel } from "$lib/integrations/meta_ads/format";

  let { data } = $props();
  const currency = $derived(data.account.currency);
  type Decision = (typeof data.decisions)[number];

  /** What the change *was*, in one line: the amounts for a budget, the fields for an edit. */
  function detail(decision: Decision): string {
    const payload = decision.payload ?? {};
    if (decision.decision === "budget_changed") {
      return t("meta_ads.decisions.budget", {
        from: fmtCents((payload.from_cents as number | null) ?? null, currency),
        to: fmtCents((payload.to_cents as number | null) ?? null, currency),
      });
    }
    if (decision.decision === "created" && typeof payload.daily_budget_cents === "number") {
      return t("meta_ads.decisions.created_with_budget", {
        amount: fmtCents(payload.daily_budget_cents, currency),
      });
    }
    return "";
  }

  /** Persist the chosen size as this list's default. The URL stays the current view. */
  function rememberSize(size: number): void {
    const body = new FormData();
    body.set("page_size", String(size));
    void fetch("?/saveTable", {
      method: "POST",
      headers: { "x-sveltekit-action": "true" },
      body,
    });
  }
</script>

<svelte:head>
  <title>{pageTitle(t("meta_ads.view.decisions"))}</title>
</svelte:head>

{#if data.loadError}
  <p class="mb-4 text-sm text-red-600 dark:text-red-400" role="alert">{t(data.loadError)}</p>
{/if}

<Card title={t("meta_ads.view.decisions")}>
  {#if data.decisions.length === 0}
    <p class="py-6 text-center text-sm text-text-muted">{t("meta_ads.decisions.empty")}</p>
  {:else}
    <ol class="divide-y divide-border">
      {#each data.decisions as decision (decision.id)}
        <li class="py-3 first:pt-0 last:pb-0">
          <p class="text-sm text-text">
            <span class="font-medium">{decision.decided_by_name || t("activity.system")}</span>
            {#if decision.impersonator_name}
              <span class="text-xs text-text-muted"
                >({t("activity.impersonated_title", { actor: decision.impersonator_name })})</span
              >
            {/if}
            <!-- One sentence per decision, with the subject and the name as its parts: a verb
                 glued in front of a noun reads as a sentence in English and as a telegram in
                 Dutch, where the verb's other half belongs at the end. -->
            {decisionLabel(decision.decision, {
              subject: subjectLabel(decision.subject_type),
              name: decision.subject_name || decision.subject_meta_id,
            })}
          </p>
          {#if detail(decision)}
            <p class="mt-0.5 text-sm tabular-nums text-text">{detail(decision)}</p>
          {/if}
          {#if decision.reason}
            <p class="mt-0.5 text-sm text-text-muted">{decision.reason}</p>
          {/if}
          <p class="mt-0.5 text-xs tabular-nums text-text-muted">
            {fmtDateTime(decision.created_at)}
          </p>
        </li>
      {/each}
    </ol>
  {/if}
</Card>

<Pagination
  total={data.total}
  page={data.paging.page}
  limit={data.paging.limit}
  onsize={rememberSize}
/>
