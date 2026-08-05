<script lang="ts">
  /**
   * The reporting panel on a client's page (issue #300, key `reporting.reports`).
   *
   * Answers the two questions an account manager has while looking at a client: *did last
   * month's report go out*, and *when is the next one*. Everything it draws came down with the
   * page in the panel provider's payload — no fetch of its own (docs/PERFORMANCE.md).
   *
   * It is also what a **portal** login sees on their own company page, which is why the schedule,
   * the recipients and every write control are behind their own flags: the API already withheld
   * them, and a control that would 403 is never drawn (docs/UX.md — `!isPortal` is not the gate,
   * the permission is).
   */
  import { CalendarClock, FileText, Plus, Settings2 } from "@lucide/svelte";

  import { getLocale } from "$lib/paraglide/runtime";
  import { t } from "$lib/core/i18n";

  import ReportStatusPill from "./ReportStatusPill.svelte";
  import { audienceLabel, cadenceLabel, deliveryLabel, fmtDate, periodLabel } from "./format";
  import type { EffectiveSchedule, ReportRow } from "./types";

  let { companyId, data }: { companyId: string; data: Record<string, unknown> } = $props();

  interface PanelData {
    forbidden?: boolean;
    reports?: ReportRow[];
    can_manage?: boolean;
    can_send?: boolean;
    schedule?: EffectiveSchedule;
    next_run_on?: string | null;
    recipients?: { email: string; name?: string }[];
    configured?: boolean;
  }
  const panel = $derived(data as PanelData);
  const reports = $derived(panel.reports ?? []);
  const canManage = $derived(Boolean(panel.can_manage));
  const schedule = $derived(panel.schedule);
  const recipients = $derived(panel.recipients ?? []);
  const locale = $derived(getLocale());
  const settingsHref = $derived(`/companies/${companyId}/reporting`);
</script>

{#if panel.forbidden}
  <p class="text-sm text-text-muted">{t("reporting.panel.forbidden")}</p>
{:else}
  <div class="space-y-4">
    {#if canManage}
      <!-- The schedule in one sentence, so nobody has to open a form to learn what happens
           next. `next_run_on` is resolved server-side in the org's own calendar. -->
      <div class="flex flex-wrap items-center gap-2 text-sm text-text-muted">
        <CalendarClock size={16} class="shrink-0" />
        {#if schedule?.cadence === "off"}
          <span>{t("reporting.panel.schedule_off")}</span>
        {:else}
          <span>
            {t("reporting.panel.schedule_line", {
              cadence: cadenceLabel(schedule?.cadence),
              delivery: deliveryLabel(schedule?.delivery),
            })}
          </span>
          {#if panel.next_run_on}
            <span class="text-text">· {fmtDate(panel.next_run_on, locale)}</span>
          {/if}
        {/if}
        <a
          href={settingsHref}
          class="ml-auto inline-flex items-center gap-1 text-brand hover:underline"
        >
          <Settings2 size={14} />
          {t("reporting.panel.configure")}
        </a>
      </div>

      {#if !panel.configured}
        <p class="rounded-lg bg-surface px-3 py-2 text-xs text-text-muted">
          {t("reporting.panel.no_profile")}
        </p>
      {:else if recipients.length === 0}
        <!-- A report nobody receives is a document generated into a drawer. Say so here rather
             than at send time, where it is already too late to be useful. -->
        <p
          class="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-200"
        >
          {t("reporting.panel.no_recipients")}
        </p>
      {/if}
    {/if}

    {#if reports.length === 0}
      <p class="text-sm text-text-muted">{t("reporting.panel.empty")}</p>
    {:else}
      <ul class="divide-y divide-border">
        {#each reports as report (report.id)}
          <li class="flex items-center gap-3 py-2">
            <FileText size={16} class="shrink-0 text-text-muted" />
            <a href={`/reports/${report.id}`} class="min-w-0 flex-1 hover:underline">
              <span class="block truncate text-sm text-text">{periodLabel(report, locale)}</span>
              <span class="block text-xs text-text-muted">
                {audienceLabel(report.audience)}
                {#if report.sent_at}· {t("reporting.panel.sent_on", {
                    date: fmtDate(report.sent_at, locale),
                  })}{/if}
              </span>
            </a>
            <ReportStatusPill status={report.status} size="xs" />
          </li>
        {/each}
      </ul>
    {/if}

    {#if canManage}
      <!-- Generating posts a form, and a form belongs to the page that owns its action. Rather
           than adding one to the shared company page for a button that is one click from its
           own screen, both controls link to the client's reporting page — where the profile
           being generated *from* is also on display. -->
      <div class="flex items-center gap-3">
        <a
          href={settingsHref}
          class="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-text hover:bg-surface"
        >
          <Plus size={14} />
          {t("reporting.panel.generate")}
        </a>
        <a href={`/reports?company=${companyId}`} class="text-sm text-brand hover:underline">
          {t("reporting.panel.all")}
        </a>
      </div>
    {/if}
  </div>
{/if}
