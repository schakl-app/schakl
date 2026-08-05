<script lang="ts">
  /**
   * The portal homepage's reporting widget (issue #300).
   *
   * A client's monthly report, on the page they already open — the summary they would otherwise
   * have to find in an e-mail attachment, plus the PDF to forward. Only ever the published,
   * client-facing ones: the API's portal repository decides that, not this component.
   *
   * The summary is shown as *prose*, dated with the period it describes. A paragraph about July
   * rendered without its period would read as a description of today, which is the one way this
   * widget could mislead.
   */
  import { Download, FileText } from "@lucide/svelte";

  import { getLocale } from "$lib/paraglide/runtime";
  import { t } from "$lib/core/i18n";
  import DashboardWidgetCard from "$lib/core/ui/DashboardWidgetCard.svelte";

  import { fmtDate, periodLabel } from "./format";
  import type { ReportDetail, ReportRow } from "./types";

  let { data }: { data: unknown } = $props();

  interface WidgetData {
    latest: ReportDetail | null;
    previous: ReportRow[];
  }
  const payload = $derived((data ?? { latest: null, previous: [] }) as WidgetData);
  const latest = $derived(payload.latest);
  const locale = $derived(getLocale());
  const summary = $derived(String((latest?.narrative as Record<string, string>)?.summary ?? ""));
</script>

<DashboardWidgetCard title={t("reporting.widget.title")} href="/reports">
  {#if !latest}
    <p class="text-sm text-text-muted">{t("reporting.widget.empty")}</p>
  {:else}
    <div class="space-y-3">
      <div class="flex items-center justify-between gap-3">
        <div class="min-w-0">
          <a
            href={`/reports/${latest.id}`}
            class="block truncate font-medium text-text hover:underline"
          >
            {periodLabel(latest, locale)}
          </a>
          {#if latest.published_at}
            <span class="text-xs text-text-muted">
              {t("reporting.widget.published_on", { date: fmtDate(latest.published_at, locale) })}
            </span>
          {/if}
        </div>
        {#if latest.pdf_file_id}
          <a
            href={`/reports/${latest.id}/pdf`}
            class="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-text hover:bg-surface"
          >
            <Download size={14} />
            {t("reporting.widget.download")}
          </a>
        {/if}
      </div>

      {#if summary}
        <p class="whitespace-pre-line text-sm leading-relaxed text-text-muted">{summary}</p>
      {/if}

      {#if payload.previous.length > 0}
        <div class="border-t border-border pt-3">
          <p class="mb-1.5 text-xs font-medium uppercase tracking-wide text-text-muted">
            {t("reporting.widget.earlier")}
          </p>
          <ul class="space-y-1">
            {#each payload.previous as report (report.id)}
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
        </div>
      {/if}
    </div>
  {/if}
</DashboardWidgetCard>
