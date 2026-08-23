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
  import {
    AlertTriangle,
    ArrowLeft,
    Download,
    Eye,
    RefreshCw,
    Send,
    Settings2,
    Trash2,
  } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { invalidate } from "$app/navigation";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { withOrigin } from "$lib/core/origin";
  import { returnHref } from "$lib/core/screen-position.svelte";
  import { pollWhile } from "$lib/core/poll.svelte";
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
  /**
   * A client gets the document; staff get the review desk (#373).
   *
   * This screen used to render one layout for everybody — per-section prose cards down the left,
   * the preview frame down the right — with the write controls hidden for a portal login. What
   * a client's monthly report therefore looked like was a half-disabled admin tool: nine
   * read-only text cards headed with our internal section names, beside a frame. They came for
   * a document.
   *
   * `isPortal` decides the **layout** only, which is the one question it is genuinely the right
   * signal for (§15's "external login is one fact", #274): a read-only staff member still wants
   * the review desk, because that is their working tool. Every control below stays gated on its
   * own API permission, so nothing depends on this flag for safety.
   */
  const reader = $derived(data.isPortal);

  const busy = new InFlight();
  let confirmDelete = $state(false);

  /**
   * Generating happens in a worker, so this screen has to ask. Without it the spinner below is
   * a still image: a run that finished forty seconds after the redirect kept saying "bezig met
   * genereren" until somebody thought to reload, which is exactly what a hung job looks like.
   * The API's own reaper bounds how long this can go on, so the interval always ends.
   */
  const generating = $derived(report.status === "generating");
  pollWhile(
    () => generating,
    () => invalidate("reporting:report"),
  );
</script>

<svelte:head>
  <title>{pageTitle(report.title || t("nav.reports"))}</title>
</svelte:head>

<a
  href={returnHref("/reports")}
  class="mb-4 inline-flex items-center gap-1.5 text-sm text-text-muted hover:text-text"
>
  <ArrowLeft size={15} />
  {t("nav.reports")}
</a>

<div class="mb-6 flex flex-wrap items-start justify-between gap-4">
  <div class="min-w-0">
    <div class="flex flex-wrap items-center gap-2">
      <h1 class="text-xl font-semibold text-text">{report.company_name}</h1>
      <!-- "Klaar om na te kijken" is a state in *our* workflow. A client reading it on their own
           monthly report learns that somebody here has not looked at it yet, which is true, none
           of their business, and alarming. Same for the audience label under it: "Klantrapportage"
           is a word for the other kind of document, and they have never seen the other kind. -->
      {#if !reader}
        <ReportStatusPill status={report.status} />
      {/if}
      {#if isInternal}
        <span
          class="rounded-full bg-red-50 px-2.5 py-1 text-xs font-semibold uppercase tracking-wide text-red-700 dark:bg-red-950 dark:text-red-300"
        >
          {t("reporting.review.internal_badge")}
        </span>
      {/if}
    </div>
    <p class="mt-1 text-sm text-text-muted">
      {periodLabel(report, locale)}{#if !reader}
        · {audienceLabel(report.audience)}{/if}
      {#if report.sent_at}
        · {t("reporting.review.sent_on", { date: fmtDate(report.sent_at, locale) })}
      {:else if report.published_at}
        · {t("reporting.review.published_on", { date: fmtDate(report.published_at, locale) })}
      {/if}
    </p>
  </div>

  <div class="flex flex-wrap items-center gap-2">
    <!-- Everything the reviewer wants to change about *the next* report — the tone, the
         recipients, the facts the model is given — lives on the client, and finding it meant
         going back to the list, opening the company and then its reporting page. Gated on the
         destination's own permission, so it is never a link that redirects on arrival. -->
    {#if data.canManageProfile}
      <a
        href={`/companies/${report.company_id}/reporting`}
        class="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-sm text-text hover:bg-surface"
      >
        <Settings2 size={15} />
        {t("reporting.review.client_settings")}
      </a>
    {/if}

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
      {#each warnings as warning (warning.code + (warning.detail ?? ""))}
        <li>{warningText(warning)}</li>
      {/each}
    </ul>
  </section>
{/if}

{#if reader}
  <!-- The client's view: the document, full width, and nothing that names our machinery. No
       section cards, no preview heading, and deliberately no summary card above the frame — the
       document's own cover already opens with that exact paragraph, and printing it twice a
       hundred pixels apart reads as a mistake rather than as emphasis. -->
  <iframe
    src={`/reports/${report.id}/preview`}
    title={report.title || t("nav.reports")}
    class="h-[80vh] w-full rounded-xl border border-border bg-white"
    sandbox="allow-same-origin"
  ></iframe>
{:else}
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
              {report.published_at
                ? t("reporting.review.unpublish")
                : t("reporting.review.publish")}
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
{/if}

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("reporting.review.delete_title")}
  message={t("reporting.review.delete_message")}
  action={withOrigin("?/delete", page.url)}
/>
