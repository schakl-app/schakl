<script lang="ts">
  /**
   * What has already been settled about this account, newest first.
   *
   * Append-only, so the same subject can appear more than once and the newest entry is the one
   * that stands. That is deliberate: "excluded in March, kept in June" is a true sequence and an
   * agency is asked about it months later. A withdrawn entry stays, greyed, with who withdrew it —
   * a delete would take the reason with it, and the reason is the whole value of the record.
   *
   * Business-licensed — see LICENSE.
   */
  import { ArrowLeft, Undo2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { fmtDateTime } from "$lib/core/format";
  import { InFlight } from "$lib/core/submit.svelte";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import Pagination from "$lib/core/ui/Pagination.svelte";

  let { data, form } = $props();

  const busy = new InFlight();

  const base = $derived(`/marketing/google-ads/${page.params.accountId}`);
  const mayWithdraw = $derived(can(page.data.user, "google_ads.policy.manage"));

  /** A filter change drops the page: page 7 of "with withdrawn" is not page 7 of without. */
  const toggleHref = $derived(
    data.includeWithdrawn ? `${base}/decisions` : `${base}/decisions?withdrawn=1`,
  );
</script>

<a href={base} class="mb-3 inline-flex items-center gap-1 text-sm text-text-muted hover:text-text">
  <ArrowLeft size={14} aria-hidden="true" />
  {t("google_ads.policy.back")}
</a>

{#if form?.key}
  <p class="mb-3 rounded-xl border border-border bg-surface-raised p-3 text-sm text-text">
    {t(form.key)}
  </p>
{/if}

<div class="mb-3 flex items-center justify-between gap-3">
  <p class="text-sm text-text-muted">{t("google_ads.decision.help")}</p>
  <a href={toggleHref} class="text-xs font-medium text-text-muted hover:text-text">
    {t(
      data.includeWithdrawn
        ? "google_ads.decision.hide_withdrawn"
        : "google_ads.decision.show_withdrawn",
    )}
  </a>
</div>

{#if data.decisions.length === 0}
  <p class="text-sm text-text-muted">{t("google_ads.decision.empty")}</p>
{:else}
  <div class="overflow-x-auto rounded-xl border border-border bg-surface-raised">
    <table class="w-full min-w-max text-sm">
      <thead>
        <tr class="border-b border-border text-left">
          <th class="px-3 py-2 text-xs font-medium text-text-muted"
            >{t("google_ads.decision.subject")}</th
          >
          <th class="px-3 py-2 text-xs font-medium text-text-muted"
            >{t("google_ads.decision.what")}</th
          >
          <th class="px-3 py-2 text-xs font-medium text-text-muted"
            >{t("google_ads.decision.reason")}</th
          >
          <th class="px-3 py-2 text-xs font-medium text-text-muted"
            >{t("google_ads.decision.scope")}</th
          >
          <th class="px-3 py-2 text-xs font-medium text-text-muted"
            >{t("google_ads.decision.by")}</th
          >
          <th class="px-3 py-2 text-xs font-medium text-text-muted"
            >{t("google_ads.decision.when")}</th
          >
          <th class="px-3 py-2"></th>
        </tr>
      </thead>
      <tbody class="divide-y divide-border">
        {#each data.decisions as item (item.id)}
          <tr class={item.withdrawn_at ? "opacity-50" : ""}>
            <td class="px-3 py-2">{item.subject}</td>
            <td class="px-3 py-2">
              {t(`google_ads.decision.${item.decision}`)}
              {#if !item.applied}
                <span class="text-xs text-text-muted">· {t("google_ads.decision.not_applied")}</span
                >
              {/if}
            </td>
            <td class="px-3 py-2 text-text-muted">{item.reason || "–"}</td>
            <td class="px-3 py-2 text-xs text-text-muted">{item.scope}</td>
            <td class="px-3 py-2 text-xs text-text-muted">
              {item.decided_by_name || t("google_ads.decision.system")}
              {#if item.impersonator_name}
                <!-- #296: an impersonated write runs as the target, so the actor alone would name
                     the client for something the agency did. -->
                <span>· {t("google_ads.decision.via", { name: item.impersonator_name })}</span>
              {/if}
            </td>
            <td class="px-3 py-2 text-xs text-text-muted">{fmtDateTime(item.created_at)}</td>
            <td class="px-3 py-2 text-right">
              {#if mayWithdraw && !item.withdrawn_at}
                <form method="POST" action="?/withdraw" use:enhance={busy.clear("withdraw")}>
                  <input type="hidden" name="decision_id" value={item.id} />
                  <button
                    type="submit"
                    class="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs font-medium text-text-muted hover:bg-surface hover:text-text"
                  >
                    <Undo2 size={12} aria-hidden="true" />
                    {t("google_ads.decision.withdraw")}
                  </button>
                </form>
              {:else if item.withdrawn_at}
                <span class="text-xs text-text-muted"
                  >{t("google_ads.decision.withdrawn_by", {
                    name: item.withdrawn_by_name ?? "",
                  })}</span
                >
              {/if}
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
{/if}

<Pagination total={data.total} page={data.paging.page} limit={data.paging.limit} />
