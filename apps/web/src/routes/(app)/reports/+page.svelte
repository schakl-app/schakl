<script lang="ts">
  /**
   * The report register (issue #300) — every client's reports, newest period first.
   *
   * Ends in the shared pager like every list (CLAUDE.md §9): a list that shows a prefix of
   * itself and apologises is the shape this contract exists to prevent.
   *
   * The screen is the same for staff and for a client login; what differs is what the API
   * serves and which controls their permissions draw. `!isPortal` is never the gate here.
   */
  import { FileText, Play, RefreshCw } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { goto, invalidate } from "$app/navigation";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { pollWhile } from "$lib/core/poll.svelte";
  import { InFlight } from "$lib/core/submit.svelte";
  import { resetPage } from "$lib/core/table/paging";
  import { pageTitle } from "$lib/core/title";
  import Button from "$lib/core/ui/Button.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import Pagination from "$lib/core/ui/Pagination.svelte";
  import ReportStatusPill from "$lib/modules/reporting/ReportStatusPill.svelte";
  import {
    audienceLabel,
    fmtDate,
    needsAttention,
    periodLabel,
  } from "$lib/modules/reporting/format";

  let { data, form } = $props();

  const busy = new InFlight();
  const reports = $derived(data.reports);
  const locale = $derived(data.locale ?? "nl");

  /**
   * "Genereer alles" queues a job per client and returns immediately, so the whole point of
   * this list right afterwards is watching the batch land. Only while something on the page is
   * actually running — the interval stops on its own when the last row leaves `generating`.
   */
  const anyGenerating = $derived(reports.some((r) => r.status === "generating"));
  pollWhile(
    () => anyGenerating,
    () => invalidate("reporting:reports"),
  );

  /** Every filter drops the page — page 7 of the old filter is not page 7 of the new one. */
  function setFilter(key: string, value: string) {
    const url = resetPage(page.url);
    if (value) url.searchParams.set(key, value);
    else url.searchParams.delete(key);
    goto(url, { keepFocus: true, noScroll: true });
  }

  const companyOptions = $derived([
    { value: "", label: t("reporting.list.all_clients") },
    ...data.companies.map((c) => ({ value: c.id, label: c.name })),
  ]);
  const audienceOptions = $derived([
    { value: "", label: t("reporting.list.all_audiences") },
    { value: "client", label: audienceLabel("client") },
    ...(data.canSeeInternal ? [{ value: "internal", label: audienceLabel("internal") }] : []),
  ]);
</script>

<svelte:head>
  <title>{pageTitle(t("nav.reports"))}</title>
</svelte:head>

<div class="mb-6 flex flex-wrap items-start justify-between gap-3">
  <div>
    <h1 class="mt-2 text-xl font-semibold text-text">{t("nav.reports")}</h1>
    <p class="text-sm text-text-muted">{t("reporting.list.subtitle")}</p>
  </div>
  {#if data.canWrite}
    <form method="POST" action="?/generateAll" use:enhance={busy.keep("all")}>
      <Button type="submit" loading={busy.is("all")} disabled={busy.active}>
        <Play size={15} />
        {t("reporting.list.generate_all")}
      </Button>
    </form>
  {/if}
</div>

{#if form?.batch}
  {#if form.batch.enrolled === 0}
    <!-- Nobody is enrolled. A bare "0" here reads as a broken button; say which step is
         missing, and how many clients are waiting for it. -->
    <p
      class="mb-4 rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-200"
    >
      {t("reporting.list.nobody_enrolled")}
      {#if form.batch.unconfigured > 0}
        {t("reporting.list.unconfigured", { count: String(form.batch.unconfigured) })}
      {/if}
    </p>
  {:else}
    <p class="mb-4 rounded-lg bg-surface px-4 py-3 text-sm text-text">
      {t("reporting.list.batch_queued", { count: String(form.batch.queued) })}
      {#if form.batch.skipped.length > 0}
        <span class="text-text-muted">
          · {t("reporting.list.batch_skipped", { count: String(form.batch.skipped.length) })}
        </span>
      {/if}
    </p>
  {/if}
{:else if form?.queued}
  <p class="mb-4 rounded-lg bg-surface px-4 py-3 text-sm text-text">
    {t("reporting.list.queued")}
  </p>
{:else if form?.error}
  <p
    class="mb-4 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950 dark:text-red-300"
  >
    {t(form.error)}
  </p>
{/if}

{#if data.canWrite}
  <!-- One filter row above everything it scopes; both drop the page. -->
  <div class="mb-4 flex flex-wrap items-center gap-3">
    <div class="w-64">
      <Combobox
        name="company"
        value={data.filters.company_id}
        items={companyOptions}
        ariaLabel={t("reporting.list.all_clients")}
        placeholder={t("reporting.list.all_clients")}
        onselect={(value: string) => setFilter("company", value)}
      />
    </div>
    <div class="w-52">
      <Combobox
        name="audience"
        value={data.filters.audience}
        items={audienceOptions}
        ariaLabel={t("reporting.list.all_audiences")}
        placeholder={t("reporting.list.all_audiences")}
        onselect={(value: string) => setFilter("audience", value)}
      />
    </div>
  </div>
{/if}

{#if reports.length === 0}
  <div class="rounded-xl border border-border bg-surface-raised px-6 py-12 text-center">
    <FileText size={28} class="mx-auto mb-3 text-text-muted" />
    <p class="text-sm text-text-muted">{t("reporting.list.empty")}</p>
    {#if data.canWrite}
      <p class="mx-auto mt-2 max-w-md text-sm text-text-muted">
        {t("reporting.list.empty_hint")}
      </p>
    {/if}
  </div>
{:else}
  <div class="overflow-x-auto rounded-xl border border-border bg-surface-raised">
    <table class="w-full text-sm">
      <thead
        class="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted"
      >
        <tr>
          <th class="px-4 py-3 font-medium">{t("reporting.list.client")}</th>
          <th class="px-4 py-3 font-medium">{t("reporting.list.period")}</th>
          <th class="px-4 py-3 font-medium">{t("reporting.list.audience")}</th>
          <th class="px-4 py-3 font-medium">{t("reporting.list.status")}</th>
          <th class="px-4 py-3 font-medium">{t("reporting.list.sent")}</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-border">
        {#each reports as report (report.id)}
          <tr class="hover:bg-surface">
            <td class="px-4 py-3">
              <a href={`/reports/${report.id}`} class="font-medium text-text hover:underline">
                {report.company_name}
              </a>
              {#if report.warning_count > 0}
                <span class="ml-2 text-xs text-amber-600 dark:text-amber-400">
                  {t("reporting.list.warnings", { count: String(report.warning_count) })}
                </span>
              {/if}
            </td>
            <td class="px-4 py-3 text-text-muted">{periodLabel(report, locale)}</td>
            <td class="px-4 py-3 text-text-muted">{audienceLabel(report.audience)}</td>
            <td class="px-4 py-3">
              <ReportStatusPill status={report.status} size="xs" />
              {#if report.status === "generating"}
                <RefreshCw size={13} class="ml-1 inline animate-spin text-text-muted" />
              {/if}
            </td>
            <td class="px-4 py-3 text-text-muted">
              {report.sent_at ? fmtDate(report.sent_at, locale) : "—"}
              {#if needsAttention(report.status) && !report.sent_at}
                <a href={`/reports/${report.id}`} class="ml-2 text-brand hover:underline">
                  {t("reporting.list.review")}
                </a>
              {/if}
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>

  <Pagination total={data.total} page={data.paging.page} limit={data.paging.limit} />
{/if}
