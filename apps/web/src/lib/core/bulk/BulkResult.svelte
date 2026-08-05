<script lang="ts">
  /**
   * What a batch actually did, above the list it did it to.
   *
   * A batch's honest answer is "37 done, 3 skipped, and here is why" (docs/UX.md, #299): the API
   * reports the rows it could not do instead of rolling the good ones back, so a banner that
   * only said "done" would be claiming work that did not happen. The reasons are the API's own
   * i18n keys, rendered straight through `t()` — the same vocabulary the single-row endpoints
   * raise, so "this task's new due date needs a reason" reads the same here as in the form.
   */
  import { t } from "$lib/core/i18n";

  import type { BulkOutcome } from "./types";

  let {
    result,
    prefix = "bulk",
  }: {
    result: BulkOutcome | null | undefined;
    /**
     * Namespace for the `done_<kind>` / `skipped` lines. The generic menu's kinds are
     * `update` / `delete`; a module with review actions of its own has its own verbs and its
     * own sentences for them ("6 goedgekeurd", not "6 bijgewerkt"), so it says which namespace
     * to read them from rather than the banner being written twice.
     */
    prefix?: string;
  } = $props();
</script>

{#if result}
  <div
    class="mb-4 rounded-lg border border-border bg-surface-raised px-4 py-3 text-sm"
    role="status"
  >
    <p class="font-medium text-text">
      {t(`${prefix}.done_${result.kind}`, { count: result.succeeded })}
      {#if result.failed > 0}
        <span class="font-normal text-text-muted">
          · {t(`${prefix}.skipped`, { count: result.failed })}
        </span>
      {/if}
    </p>
    {#if result.reasons.length > 0}
      <ul class="mt-1 space-y-0.5 text-xs text-text-muted">
        {#each result.reasons as reason (reason)}
          <li>{t(reason)}</li>
        {/each}
      </ul>
    {/if}
  </div>
{/if}
