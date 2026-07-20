<script lang="ts">
  import { goto } from "$app/navigation";
  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";

  let { data } = $props();

  /** Run errors are either engine i18n keys (`errors.*`) or raw upstream data (HTTP 500). */
  function errorText(error: string | null | undefined): string {
    if (!error) return "";
    return error.startsWith("errors.") && !error.includes(" ") ? t(error) : error;
  }

  /** `steps` is opaque JSONB in the schema; this is its recorded shape (executor.py). */
  type Step = { action_type: string; status: string; error?: string; result?: unknown };
  function stepsOf(run: { steps: unknown[] }): Step[] {
    return run.steps as Step[];
  }

  const STATUS_CLASS: Record<string, string> = {
    pending: "bg-surface text-text-muted border border-border",
    running: "bg-blue-100 text-blue-800",
    succeeded: "bg-green-100 text-green-800",
    failed: "bg-red-100 text-red-800",
    skipped: "bg-amber-100 text-amber-800",
  };

  function onFilter(event: Event) {
    const value = (event.currentTarget as HTMLSelectElement).value;
    void goto(value ? `?rule_id=${value}` : "?", { keepFocus: true });
  }
</script>

<svelte:head>
  <title>{pageTitle(t("automation.runs"))}</title>
</svelte:head>

<div class="mb-6">
  <div class="mt-2 flex flex-wrap items-center gap-3">
    <h1 class="text-xl font-semibold text-text">{t("automation.runs")}</h1>
    <div class="flex-1"></div>
    {#if data.rules.length > 0}
      <label class="flex items-center gap-2 text-sm text-text-muted">
        {t("automation.run_rule")}
        <select
          value={data.ruleId ?? ""}
          onchange={onFilter}
          class="rounded-lg border border-border bg-surface px-2 py-1.5 text-sm text-text"
        >
          <option value="">{t("automation.all_rules")}</option>
          {#each data.rules as rule (rule.id)}
            <option value={rule.id}>{rule.name}</option>
          {/each}
        </select>
      </label>
    {/if}
  </div>
  <p class="mt-1 text-sm text-text-muted">{t("automation.runs_subtitle")}</p>
</div>

<div class="rounded-xl border border-border bg-surface-raised">
  {#if data.page.items.length === 0}
    <p class="p-6 text-center text-sm text-text-muted">{t("automation.runs_empty")}</p>
  {:else}
    <ul>
      {#each data.page.items as run (run.id)}
        <li class="border-b border-border last:border-b-0">
          <details>
            <summary
              class="flex cursor-pointer flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 text-sm"
            >
              <span
                class="rounded-full px-2 py-0.5 text-xs font-medium {STATUS_CLASS[run.status] ??
                  STATUS_CLASS.pending}"
              >
                {t(`automation.status.${run.status}`)}
              </span>
              <span class="font-medium text-text">{run.rule_name}</span>
              <span class="text-text-muted">{t(`automation.trigger.${run.trigger_event}`)}</span>
              <span class="flex-1"></span>
              <span class="text-xs text-text-muted">{fmtDateTime(run.created_at)}</span>
            </summary>
            <div class="space-y-2 px-4 pb-4 text-sm">
              {#if run.depth > 0}
                <p class="text-xs text-text-muted">
                  {t("automation.run_depth", { depth: run.depth })}
                </p>
              {/if}
              {#if run.error}
                <p class="text-red-600">{errorText(run.error)}</p>
              {/if}
              {#if run.steps.length > 0}
                <div>
                  <h3 class="text-xs font-semibold uppercase tracking-wide text-text-muted">
                    {t("automation.run_steps")}
                  </h3>
                  <ol class="mt-1 space-y-1">
                    {#each stepsOf(run) as step, index (index)}
                      <li class="flex flex-wrap items-center gap-2">
                        <span
                          class="rounded-full px-2 py-0.5 text-xs {step.status === 'succeeded'
                            ? STATUS_CLASS.succeeded
                            : STATUS_CLASS.failed}"
                        >
                          {t(`automation.status.${step.status}`)}
                        </span>
                        <span class="text-text">
                          {t(`automation.action.${step.action_type}`)}
                        </span>
                        {#if step.error}
                          <span class="text-xs text-red-600">{errorText(step.error)}</span>
                        {/if}
                        {#if step.result}
                          <code
                            class="max-w-full overflow-x-auto rounded bg-surface px-1.5 py-0.5 text-xs text-text-muted"
                            >{JSON.stringify(step.result)}</code
                          >
                        {/if}
                      </li>
                    {/each}
                  </ol>
                </div>
              {/if}
            </div>
          </details>
        </li>
      {/each}
    </ul>
  {/if}
</div>
