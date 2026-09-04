<script lang="ts">
  /**
   * The portal homepage's reporting widget (issue #300; a preview since the team's review).
   *
   * It used to print the report's own prose — the summary and every section's paragraph — on
   * the homepage, which made the tile a second copy of the document with none of its numbers,
   * and a client who had already read the report re-read it on every visit. Now the tile is the
   * document's **cover**: one sentence saying which month this is the report of, the first page
   * scaled down behind it (the same `/preview` the report page frames, so what is shown is what
   * they open), and the two ways in. Nothing written *in* the report is repeated here.
   *
   * Only ever the published, client-facing ones: the API's portal repository decides that, not
   * this component — and on the selected company, because the board is one company at a time.
   */
  import { Download, FileText } from "@lucide/svelte";

  import { getLocale } from "$lib/paraglide/runtime";
  import { t } from "$lib/core/i18n";
  import DashboardWidgetCard from "$lib/core/ui/DashboardWidgetCard.svelte";
  import PanelRows from "$lib/core/ui/PanelRows.svelte";

  import { fmtDate, periodLabel } from "./format";
  import type { ReportRow } from "./types";

  let { data }: { data: unknown } = $props();

  interface WidgetData {
    latest: ReportRow | null;
    previous: ReportRow[];
    /** Every report this client may read, not the four listed (#407). */
    total?: number;
  }
  const payload = $derived((data ?? { latest: null, previous: [] }) as WidgetData);
  const earlierTotal = $derived(
    payload.total != null ? Math.max(0, payload.total - 1) : payload.previous.length,
  );
  const latest = $derived(payload.latest);
  const locale = $derived(getLocale());

  // An A4 page, scaled to the strip it sits in: the frame renders the document at its own
  // width and is transformed down, so the cover reads as a page and not as a squeezed site.
  const PAGE_WIDTH = 794; // A4 at 96dpi
  const PREVIEW_WIDTH = 260;
  const PREVIEW_HEIGHT = 340;
  const scale = PREVIEW_WIDTH / PAGE_WIDTH;

  const buttonClass =
    "inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-text hover:bg-surface";
</script>

<DashboardWidgetCard title={t("reporting.widget.title")} href="/reports">
  {#if !latest}
    <p class="text-sm text-text-muted">{t("reporting.widget.empty")}</p>
  {:else}
    <div class="flex flex-col gap-4 sm:flex-row sm:items-start">
      <!-- The cover, as a link: the whole page is the way in, and the frame itself takes no
           pointer events so a click lands on the link rather than inside the document. -->
      <a
        href={`/reports/${latest.id}`}
        class="group relative block shrink-0 overflow-hidden rounded-lg border border-border bg-white shadow-sm"
        style={`width:${PREVIEW_WIDTH}px;height:${PREVIEW_HEIGHT}px`}
        aria-label={t("reporting.widget.open")}
      >
        <iframe
          src={`/reports/${latest.id}/preview`}
          title={periodLabel(latest, locale)}
          tabindex="-1"
          aria-hidden="true"
          loading="lazy"
          sandbox="allow-same-origin"
          class="pointer-events-none origin-top-left border-0"
          style={`width:${PAGE_WIDTH}px;height:${Math.round(PREVIEW_HEIGHT / scale)}px;transform:scale(${scale})`}
        ></iframe>
        <span
          class="pointer-events-none absolute inset-0 bg-transparent transition group-hover:bg-black/5"
        ></span>
      </a>

      <div class="min-w-0 flex-1 space-y-3">
        <div>
          <a
            href={`/reports/${latest.id}`}
            class="block text-lg font-semibold text-text hover:underline"
          >
            {periodLabel(latest, locale)}
          </a>
          <p class="text-sm text-text-muted">
            {t("reporting.widget.this_is_the_report", { period: periodLabel(latest, locale) })}
          </p>
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

        {#if payload.previous.length > 0}
          <div class="border-t border-border pt-3">
            <p class="mb-1.5 text-xs font-medium uppercase tracking-wide text-text-muted">
              {t("reporting.widget.earlier")}
            </p>
            <PanelRows rows={payload.previous} total={earlierTotal} href="/reports">
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
    </div>
  {/if}
</DashboardWidgetCard>
