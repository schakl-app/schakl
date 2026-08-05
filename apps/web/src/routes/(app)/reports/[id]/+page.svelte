<script lang="ts">
  /**
   * The review screen (issue #300): read what the client will read, fix it, send it.
   *
   * Two halves. The left is the prose — a summary plus one editable paragraph per section, in
   * the order the document prints them. The right is the document itself, framed from
   * `/preview`, which is the *same* artefact the PDF prints.
   *
   * The warnings panel is the agency's own: stale sources, a truncated table, a phrase the
   * tone of voice excludes. It is never part of the document and a client never sees it — the
   * API strips it for a portal caller, and this screen only draws what it was sent.
   */
  import { AlertTriangle, ArrowLeft, Download, Eye, RefreshCw, Send, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import Button from "$lib/core/ui/Button.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import ReportSectionEditor from "$lib/modules/reporting/ReportSectionEditor.svelte";
  import ReportStatusPill from "$lib/modules/reporting/ReportStatusPill.svelte";
  import { audienceLabel, fmtDate, periodLabel, warningText } from "$lib/modules/reporting/format";
  import type { ReportSectionRef, ReportWarning } from "$lib/modules/reporting/types";

  let { data, form } = $props();

  const report = $derived(data.report);
  const locale = $derived(data.locale ?? "nl");
  const narrative = $derived((report.narrative ?? {}) as Record<string, string>);
  const sections = $derived((report.sections ?? []) as unknown as ReportSectionRef[]);
  const warnings = $derived((report.warnings ?? []) as unknown as ReportWarning[]);
  const edited = $derived(new Set(report.edited_sections ?? []));
  const isInternal = $derived(report.audience === "internal");
  const canEdit = $derived(data.canWrite && report.status !== "sent");
  const readyToSend = $derived(report.status === "ready" || report.status === "sent");

  const busy = new InFlight();
  let confirmDelete = $state(false);
</script>

<svelte:head>
  <title>{pageTitle(report.title || t("nav.reports"))}</title>
</svelte:head>

<a
  href="/reports"
  class="mb-4 inline-flex items-center gap-1.5 text-sm text-text-muted hover:text-text"
>
  <ArrowLeft size={15} />
  {t("nav.reports")}
</a>

<div class="mb-6 flex flex-wrap items-start justify-between gap-4">
  <div class="min-w-0">
    <div class="flex flex-wrap items-center gap-2">
      <h1 class="text-xl font-semibold text-text">{report.company_name}</h1>
      <ReportStatusPill status={report.status} />
      {#if isInternal}
        <span
          class="rounded-full bg-red-50 px-2.5 py-1 text-xs font-semibold uppercase tracking-wide text-red-700 dark:bg-red-950 dark:text-red-300"
        >
          {t("reporting.review.internal_badge")}
        </span>
      {/if}
    </div>
    <p class="mt-1 text-sm text-text-muted">
      {periodLabel(report, locale)} · {audienceLabel(report.audience)}
      {#if report.sent_at}
        · {t("reporting.review.sent_on", { date: fmtDate(report.sent_at, locale) })}
      {:else if report.published_at}
        · {t("reporting.review.published_on", { date: fmtDate(report.published_at, locale) })}
      {/if}
    </p>
  </div>

  <div class="flex flex-wrap items-center gap-2">
    {#if report.pdf_file_id}
      <a
        href={`/reports/${report.id}/pdf`}
        class="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-sm text-text hover:bg-surface"
      >
        <Download size={15} />
        {t("reporting.review.pdf")}
      </a>
    {/if}

    {#if data.canWrite && report.status !== "sent"}
      <form method="POST" action="?/regenerate" use:enhance={busy.keep("regen")}>
        <input type="hidden" name="company_id" value={report.company_id} />
        <input type="hidden" name="audience" value={report.audience} />
        <Button type="submit" variant="secondary" loading={busy.is("regen")} disabled={busy.active}>
          <RefreshCw size={15} />
          {t("reporting.review.regenerate")}
        </Button>
      </form>
    {/if}

    {#if data.canSend && !isInternal && readyToSend}
      <form method="POST" action="?/send" use:enhance={busy.keep("send")}>
        <Button type="submit" loading={busy.is("send")} disabled={busy.active}>
          <Send size={15} />
          {report.sent_at ? t("reporting.review.send_again") : t("reporting.review.send")}
        </Button>
      </form>
    {/if}

    {#if data.canWrite && !report.sent_at}
      <button
        type="button"
        class="rounded-lg p-2 text-text-muted hover:bg-surface hover:text-red-600"
        aria-label={t("common.delete")}
        onclick={() => (confirmDelete = true)}
      >
        <Trash2 size={16} />
      </button>
    {/if}
  </div>
</div>

{#if form?.error}
  <p
    class="mb-4 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950 dark:text-red-300"
  >
    {t(form.error)}
  </p>
{:else if form?.sent}
  <p
    class="mb-4 rounded-lg bg-green-50 px-4 py-3 text-sm text-green-700 dark:bg-green-950 dark:text-green-300"
  >
    {t("reporting.review.send_ok")}
  </p>
{:else if form?.queued}
  <p class="mb-4 rounded-lg bg-surface px-4 py-3 text-sm text-text">
    {t("reporting.review.regenerating")}
  </p>
{/if}

{#if report.status === "generating"}
  <p class="mb-4 flex items-center gap-2 rounded-lg bg-surface px-4 py-3 text-sm text-text-muted">
    <RefreshCw size={15} class="animate-spin" />
    {t("reporting.review.generating")}
  </p>
{/if}

{#if warnings.length > 0}
  <!-- The agency's own notes about this run. Never printed on the client's document (§17). -->
  <section
    class="mb-6 rounded-xl border border-amber-200 bg-amber-50 p-4 dark:border-amber-900 dark:bg-amber-950"
  >
    <h2
      class="mb-2 flex items-center gap-2 text-sm font-semibold text-amber-900 dark:text-amber-200"
    >
      <AlertTriangle size={15} />
      {t("reporting.review.warnings")}
    </h2>
    <ul class="space-y-1 text-sm text-amber-800 dark:text-amber-300">
      {#each warnings as warning (warning.code + (warning.detail ?? ''))}
        <li>{warningText(warning)}</li>
      {/each}
    </ul>
  </section>
{/if}

<div class="grid gap-6 lg:grid-cols-2">
  <div class="space-y-4">
    <ReportSectionEditor
      sectionKey="summary"
      title={t("reporting.doc.summary")}
      text={narrative.summary ?? ""}
      edited={edited.has("summary")}
      canWrite={canEdit}
    />

    {#each sections as section (section.key)}
      <ReportSectionEditor
        sectionKey={section.key}
        title={section.title}
        text={narrative[section.key] ?? ""}
        edited={edited.has(section.key)}
        canWrite={canEdit}
      />
    {/each}

    {#if isInternal}
      <ReportSectionEditor
        sectionKey="actions"
        title={t("reporting.doc.actions")}
        text={narrative.actions ?? ""}
        edited={edited.has("actions")}
        canWrite={canEdit}
      />
      <ReportSectionEditor
        sectionKey="questions"
        title={t("reporting.doc.questions")}
        text={narrative.questions ?? ""}
        edited={edited.has("questions")}
        canWrite={canEdit}
      />
    {/if}

    {#if sections.length === 0 && report.status !== "generating"}
      <p class="rounded-xl border border-border bg-surface-raised p-6 text-sm text-text-muted">
        {t("reporting.review.no_sections")}
      </p>
    {/if}
  </div>

  <!-- The document, exactly as it prints. Sticky so the prose on the left can be scrolled
       against it. -->
  <div class="lg:sticky lg:top-4 lg:self-start">
    <div class="mb-2 flex items-center justify-between">
      <h2 class="flex items-center gap-2 text-sm font-semibold text-text">
        <Eye size={15} />
        {t("reporting.review.preview")}
      </h2>
      {#if !isInternal && data.canSend}
        <form method="POST" action="?/publish" use:enhance={busy.keep("publish")}>
          <input type="hidden" name="published" value={report.published_at ? "false" : "true"} />
          <Button
            type="submit"
            variant="secondary"
            size="sm"
            loading={busy.is("publish")}
            disabled={busy.active || report.status === "draft"}
          >
            {report.published_at ? t("reporting.review.unpublish") : t("reporting.review.publish")}
          </Button>
        </form>
      {/if}
    </div>
    <iframe
      src={`/reports/${report.id}/preview`}
      title={t("reporting.review.preview")}
      class="h-[70vh] w-full rounded-xl border border-border bg-white"
      sandbox="allow-same-origin"
    ></iframe>
  </div>
</div>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("reporting.review.delete_title")}
  message={t("reporting.review.delete_message")}
  action="?/delete"
/>
