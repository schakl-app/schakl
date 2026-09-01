<script lang="ts">
  /**
   * The portal homepage's reporting widget (issue #300, laid out as a document since #452).
   *
   * A client's monthly report, on the page they already open. It used to print the summary as
   * one grey `whitespace-pre-line` paragraph and nothing else — six sentences with no period
   * heading beyond the link, none of the per-section prose the document carries, no way in but
   * the PDF. The prompt deliberately asks the model for prose without markdown, so there is
   * nothing to *render*: the widget has to give the document its shape. So it reads as the
   * report's front page: the period and its publication date, the summary as real paragraphs
   * in body colour, each section under its own heading, the two ways in (open, download), and
   * the earlier reports as a list.
   *
   * Only ever the published, client-facing ones: the API's portal repository decides that, not
   * this component. And the summary is dated with the period it describes — a paragraph about
   * July rendered without its period would read as a description of today.
   */
  import { Download, FileText } from "@lucide/svelte";

  import { getLocale } from "$lib/paraglide/runtime";
  import { t } from "$lib/core/i18n";
  import DashboardWidgetCard from "$lib/core/ui/DashboardWidgetCard.svelte";
  import PanelRows from "$lib/core/ui/PanelRows.svelte";

  import { fmtDate, periodLabel } from "./format";
  import type { ReportDetail, ReportRow, ReportSectionRef } from "./types";

  let { data }: { data: unknown } = $props();

  interface WidgetData {
    latest: ReportDetail | null;
    previous: ReportRow[];
    /** Every report this client may read, not the four listed (#407). */
    total?: number;
  }
  const payload = $derived((data ?? { latest: null, previous: [] }) as WidgetData);
  // The earlier-reports list is the tail of a page of four; a fifth existed and nothing
  // said so, because the load asked for `count: false`.
  const earlierTotal = $derived(
    payload.total != null ? Math.max(0, payload.total - 1) : payload.previous.length,
  );
  const latest = $derived(payload.latest);
  const locale = $derived(getLocale());
  const narrative = $derived((latest?.narrative ?? {}) as Record<string, string>);

  /** Prose comes back as plain text with blank lines between paragraphs; give each its own `<p>`. */
  function paragraphs(text: string | undefined): string[] {
    return String(text ?? "")
      .split(/\n\s*\n|\n/)
      .map((part) => part.trim())
      .filter(Boolean);
  }
  const summary = $derived(paragraphs(narrative.summary));
  // In the document's own order, and only the sections that were actually written for it.
  const sections = $derived(
    ((latest?.sections ?? []) as unknown as ReportSectionRef[])
      .map((section) => ({ ...section, paragraphs: paragraphs(narrative[section.key]) }))
      .filter((section) => section.paragraphs.length > 0),
  );

  const buttonClass =
    "inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-text hover:bg-surface";
</script>

<DashboardWidgetCard title={t("reporting.widget.title")} href="/reports">
  {#if !latest}
    <p class="text-sm text-text-muted">{t("reporting.widget.empty")}</p>
  {:else}
    <div class="space-y-4">
      <!-- The front page: the period is the title, because it is the one fact the prose below
           cannot be read without. -->
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div class="min-w-0">
          <a
            href={`/reports/${latest.id}`}
            class="block text-lg font-semibold text-text hover:underline"
          >
            {periodLabel(latest, locale)}
          </a>
          {#if latest.published_at}
            <span class="text-xs text-text-muted">
              {t("reporting.widget.published_on", { date: fmtDate(latest.published_at, locale) })}
            </span>
          {/if}
        </div>
        <div class="flex flex-wrap gap-2">
          <a href={`/reports/${latest.id}`} class={buttonClass}>
            <FileText size={14} />
            {t("reporting.widget.open")}
          </a>
          {#if latest.pdf_file_id}
            <a href={`/reports/${latest.id}/pdf`} class={buttonClass}>
              <Download size={14} />
              {t("reporting.widget.download")}
            </a>
          {/if}
        </div>
      </div>

      {#if summary.length > 0}
        <div class="space-y-2">
          {#each summary as paragraph, index (index)}
            <p class="text-sm leading-relaxed text-text">{paragraph}</p>
          {/each}
        </div>
      {/if}

      {#each sections as section (section.key)}
        <section class="border-t border-border pt-3">
          <h3 class="mb-1.5 text-sm font-semibold text-text">{section.title}</h3>
          <div class="space-y-2">
            {#each section.paragraphs as paragraph, index (index)}
              <p class="text-sm leading-relaxed text-text-muted">{paragraph}</p>
            {/each}
          </div>
        </section>
      {/each}

      {#if payload.previous.length > 0}
        <div class="border-t border-border pt-3">
          <p class="mb-1.5 text-xs font-medium uppercase tracking-wide text-text-muted">
            {t("reporting.widget.earlier")}
          </p>
          <PanelRows rows={payload.previous} total={earlierTotal}>
            {#snippet children(shown)}
              <ul class="space-y-1">
                {#each shown as report (report.id)}
                  <li>
                    <a
                      href={`/reports/${report.id}`}
                      class="inline-flex items-center gap-1.5 text-sm text-text-muted hover:text-text hover:underline"
                    >
                      <FileText size={13} />
                      {periodLabel(report, locale)}
                    </a>
                  </li>
                {/each}
              </ul>
            {/snippet}
          </PanelRows>
        </div>
      {/if}
    </div>
  {/if}
</DashboardWidgetCard>
