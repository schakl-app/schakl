<script lang="ts">
  /**
   * A client's reporting profile (issue #300).
   *
   * This screen is the spreadsheet, killed. Klantnaam, Website, Logo, Verantwoordelijke, the
   * GA4 property and the SE Ranking project all live elsewhere in the CRM already — what is
   * genuinely new is on this page: what this client does, what they are trying to achieve, who
   * receives the report, in which language, and on what schedule.
   *
   * Every schedule field may be left empty, and empty means *inherit* — the org default
   * decides, and changing it reaches every client that never chose. The placeholder on each
   * field says what will happen if it stays blank, so a form full of blanks is readable rather
   * than mysterious.
   */
  import { ArrowLeft, FileText, Play } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import Button from "$lib/core/ui/Button.svelte";
  import FormCheckbox from "$lib/core/ui/FormCheckbox.svelte";
  import ReportStatusPill from "$lib/modules/reporting/ReportStatusPill.svelte";
  import {
    audienceLabel,
    cadenceLabel,
    deliveryLabel,
    fmtDate,
    periodLabel,
  } from "$lib/modules/reporting/format";
  import type { ReportTemplate, ReportTone } from "$lib/modules/reporting/types";

  let { data, form } = $props();

  const busy = new InFlight();
  const profile = $derived(data.profile);
  const own = $derived((profile?.schedule ?? {}) as Record<string, unknown>);
  const effective = $derived((profile?.effective_schedule ?? {}) as Record<string, unknown>);
  const locale = $derived(data.locale ?? "nl");

  const clientTemplates = $derived(
    (data.templates as ReportTemplate[]).filter((tpl) => tpl.audience === "client"),
  );
  const internalTemplates = $derived(
    (data.templates as ReportTemplate[]).filter((tpl) => tpl.audience === "internal"),
  );

  /** Which contact ids the profile already sends to. */
  const chosen = $derived(
    new Set(
      ((profile?.recipients ?? []) as { contact_id?: string | null }[])
        .map((r) => r.contact_id)
        .filter(Boolean) as string[],
    ),
  );
  /** Addresses on the profile that belong to no contact — kept, and shown for editing. */
  const extras = $derived(
    ((profile?.recipients ?? []) as { contact_id?: string | null; email: string }[])
      .filter((r) => !r.contact_id)
      .map((r) => r.email)
      .join("\n"),
  );

  const inputClass =
    "w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";
  const areaClass = `${inputClass} leading-relaxed`;

  /** The fact fields, in the order somebody filling this in would think of them. */
  const FACTS = [
    "business_context",
    "goals",
    "seo_focus",
    "sea_focus",
    "key_services",
    "priority_pages",
    "conversion_goals",
    "scope_notes",
    "avoid_topics",
  ] as const;
</script>

<svelte:head>
  <title>{pageTitle(t("reporting.profile.title"))}</title>
</svelte:head>

<a
  href={`/companies/${data.companyId}`}
  class="mb-4 inline-flex items-center gap-1.5 text-sm text-text-muted hover:text-text"
>
  <ArrowLeft size={15} />
  {t("reporting.profile.back")}
</a>

<h1 class="mb-1 text-xl font-semibold text-text">{t("reporting.profile.title")}</h1>
<p class="mb-6 text-sm text-text-muted">{t("reporting.profile.subtitle")}</p>

{#if form?.saved}
  <p
    class="mb-4 rounded-lg bg-green-50 px-4 py-3 text-sm text-green-700 dark:bg-green-950 dark:text-green-300"
  >
    {t("settings.reporting.saved")}
  </p>
{:else if form?.queued}
  <p class="mb-4 rounded-lg bg-surface px-4 py-3 text-sm text-text">{t("reporting.list.queued")}</p>
{:else if form?.error}
  <p
    class="mb-4 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950 dark:text-red-300"
  >
    {t(form.error)}
  </p>
{/if}

<form method="POST" action="?/save" use:enhance={busy.keep("profile")} class="max-w-3xl space-y-8">
  <!-- ── What is true about this client ─────────────────────────────────── -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="mb-1 text-base font-semibold text-text">{t("reporting.profile.facts")}</h2>
    <p class="mb-4 text-sm text-text-muted">{t("reporting.profile.facts_hint")}</p>

    <div class="space-y-4">
      {#each FACTS as field (field)}
        <div>
          <label for={field} class="mb-1 block text-sm font-medium text-text">
            {t(`reporting.profile.${field}`)}
          </label>
          <textarea id={field} name={field} rows="2" class={areaClass}
            >{profile?.[field] ?? ""}</textarea
          >
          <p class="mt-1 text-xs text-text-muted">{t(`reporting.profile.${field}_hint`)}</p>
        </div>
      {/each}
    </div>
  </section>

  <!-- ── Voice, language and design ─────────────────────────────────────── -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="mb-1 text-base font-semibold text-text">{t("reporting.profile.voice")}</h2>
    <p class="mb-4 text-sm text-text-muted">{t("reporting.profile.voice_hint")}</p>

    <!-- The name the document carries. First, because it is the first thing a reader sees and
         the one field on this page whose absence is invisible until a report has gone out
         under a legal name nobody uses in conversation. -->
    <div class="mb-4">
      <label for="display_name" class="mb-1 block text-sm font-medium text-text">
        {t("reporting.profile.display_name")}
      </label>
      <input
        id="display_name"
        name="display_name"
        class={inputClass}
        placeholder={data.companyName}
        value={profile?.display_name ?? ""}
      />
      <p class="mt-1 text-xs text-text-muted">{t("reporting.profile.display_name_hint")}</p>
    </div>

    <div class="grid gap-4 sm:grid-cols-2">
      <div>
        <label for="tone_id" class="mb-1 block text-sm font-medium text-text">
          {t("reporting.profile.tone")}
        </label>
        <select id="tone_id" name="tone_id" class={inputClass} value={profile?.tone_id ?? ""}>
          <option value="">{t("reporting.profile.inherit")}</option>
          {#each data.tones as tone (tone.id)}
            <option value={tone.id}>{(tone as ReportTone).name}</option>
          {/each}
        </select>
      </div>
      <div>
        <label for="locale" class="mb-1 block text-sm font-medium text-text">
          {t("reporting.profile.locale")}
        </label>
        <select id="locale" name="locale" class={inputClass} value={profile?.locale ?? "nl"}>
          <option value="nl">Nederlands</option>
          <option value="en">English</option>
        </select>
        <p class="mt-1 text-xs text-text-muted">{t("reporting.profile.locale_hint")}</p>
      </div>
      <div>
        <label for="template_id" class="mb-1 block text-sm font-medium text-text">
          {t("reporting.profile.template")}
        </label>
        <select
          id="template_id"
          name="template_id"
          class={inputClass}
          value={profile?.template_id ?? ""}
        >
          <option value="">{t("reporting.profile.inherit")}</option>
          {#each clientTemplates as tpl (tpl.id)}
            <option value={tpl.id}>{tpl.name}</option>
          {/each}
        </select>
      </div>
      <div>
        <label for="internal_template_id" class="mb-1 block text-sm font-medium text-text">
          {t("reporting.profile.internal_template")}
        </label>
        <select
          id="internal_template_id"
          name="internal_template_id"
          class={inputClass}
          value={profile?.internal_template_id ?? ""}
        >
          <option value="">{t("reporting.profile.inherit")}</option>
          {#each internalTemplates as tpl (tpl.id)}
            <option value={tpl.id}>{tpl.name}</option>
          {/each}
        </select>
      </div>
    </div>
  </section>

  <!-- ── Recipients ─────────────────────────────────────────────────────── -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="mb-1 text-base font-semibold text-text">{t("reporting.profile.recipients")}</h2>
    <p class="mb-4 text-sm text-text-muted">{t("reporting.profile.recipients_hint")}</p>

    {#if data.contacts.length === 0}
      <p class="mb-3 text-sm text-text-muted">{t("reporting.profile.no_contacts")}</p>
    {:else}
      <div class="mb-4 space-y-2">
        {#each data.contacts as contact (contact.id)}
          <label class="flex items-center gap-2 text-sm text-text">
            <FormCheckbox
              name="recipient"
              value={`${contact.id}|${contact.email}|${contact.name}`}
              checked={chosen.has(contact.id)}
              class="rounded border-border"
            />
            <span>
              {contact.name}
              <span class="text-text-muted">· {contact.email}</span>
            </span>
          </label>
        {/each}
      </div>
    {/if}

    <div>
      <label for="extra_recipients" class="mb-1 block text-sm font-medium text-text">
        {t("reporting.profile.extra_recipients")}
      </label>
      <textarea id="extra_recipients" name="extra_recipients" rows="2" class={areaClass}
        >{extras}</textarea
      >
      <p class="mt-1 text-xs text-text-muted">{t("reporting.profile.extra_recipients_hint")}</p>
    </div>
  </section>

  <!-- ── Schedule ───────────────────────────────────────────────────────── -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="mb-1 text-base font-semibold text-text">{t("reporting.profile.schedule")}</h2>
    <p class="mb-4 text-sm text-text-muted">
      {t("reporting.profile.schedule_hint", {
        cadence: cadenceLabel(String(effective.cadence ?? "monthly")),
        delivery: deliveryLabel(String(effective.delivery ?? "review")),
      })}
      {#if profile?.next_run_on}
        <span class="text-text">· {fmtDate(profile.next_run_on, locale)}</span>
      {/if}
    </p>

    <div class="grid gap-4 sm:grid-cols-3">
      <div>
        <label for="p-cadence" class="mb-1 block text-sm font-medium text-text">
          {t("settings.reporting.cadence")}
        </label>
        <select id="p-cadence" name="cadence" class={inputClass} value={own.cadence ?? ""}>
          <option value="">{t("reporting.profile.inherit")}</option>
          <option value="monthly">{t("reporting.cadence.monthly")}</option>
          <option value="quarterly">{t("reporting.cadence.quarterly")}</option>
          <option value="off">{t("reporting.cadence.off")}</option>
        </select>
      </div>
      <div>
        <label for="p-day" class="mb-1 block text-sm font-medium text-text">
          {t("settings.reporting.day_of_month")}
        </label>
        <input
          id="p-day"
          name="day_of_month"
          type="number"
          min="1"
          max="28"
          class={inputClass}
          placeholder={String(effective.day_of_month ?? 5)}
          value={own.day_of_month ?? ""}
        />
      </div>
      <div>
        <label for="p-delivery" class="mb-1 block text-sm font-medium text-text">
          {t("settings.reporting.delivery")}
        </label>
        <select id="p-delivery" name="delivery" class={inputClass} value={own.delivery ?? ""}>
          <option value="">{t("reporting.profile.inherit")}</option>
          <option value="review">{t("reporting.delivery.review")}</option>
          <option value="auto">{t("reporting.delivery.auto")}</option>
        </select>
      </div>
      <!-- Three-way, and a select rather than a checkbox for that reason: the profile is
           allowed to say nothing and follow the org default, and a box cannot tell "off"
           apart from "unset". The save action read a per-client override this page had never
           drawn, so the setting existed everywhere except where anyone could reach it. -->
      <div>
        <label for="p-portal" class="mb-1 block text-sm font-medium text-text">
          {t("settings.reporting.publish_to_portal")}
        </label>
        <select
          id="p-portal"
          name="publish_to_portal"
          class={inputClass}
          value={own.publish_to_portal === undefined || own.publish_to_portal === null
            ? ""
            : String(own.publish_to_portal)}
        >
          <option value="">{t("reporting.profile.inherit")}</option>
          <option value="true">{t("common.yes")}</option>
          <option value="false">{t("common.no")}</option>
        </select>
      </div>
    </div>

    <input type="hidden" name="hour" value={own.hour ?? ""} />
    <input type="hidden" name="compare" value={own.compare ?? ""} />

    <div class="mt-4 space-y-2">
      <label class="flex items-start gap-2 text-sm text-text">
        <FormCheckbox
          name="internal_enabled"
          checked={profile?.internal_enabled ?? true}
          class="mt-0.5 rounded border-border"
        />
        <span>
          {t("reporting.profile.internal_enabled")}
          <span class="mt-0.5 block text-xs text-text-muted">
            {t("reporting.profile.internal_enabled_hint")}
          </span>
        </span>
      </label>
      <label class="flex items-start gap-2 text-sm text-text">
        <FormCheckbox
          name="active"
          checked={profile?.active ?? true}
          class="mt-0.5 rounded border-border"
        />
        <span>
          {t("reporting.profile.active")}
          <span class="mt-0.5 block text-xs text-text-muted">
            {t("reporting.profile.active_hint")}
          </span>
        </span>
      </label>
    </div>
  </section>

  <Button type="submit" loading={busy.is("profile")} disabled={busy.active}>
    {t("common.save")}
  </Button>
</form>

<!-- ── This client's reports ────────────────────────────────────────────── -->
<section class="mt-10 max-w-3xl">
  <div class="mb-3 flex items-center justify-between">
    <h2 class="text-base font-semibold text-text">{t("reporting.profile.reports")}</h2>
    {#if data.canWrite}
      <form method="POST" action="?/generate" use:enhance={busy.keep("gen")}>
        <input type="hidden" name="audience" value="client" />
        <Button
          type="submit"
          variant="secondary"
          size="sm"
          loading={busy.is("gen")}
          disabled={busy.active}
        >
          <Play size={14} />
          {t("reporting.panel.generate")}
        </Button>
      </form>
    {/if}
  </div>

  {#if data.reports.length === 0}
    <p class="rounded-xl border border-border bg-surface-raised p-6 text-sm text-text-muted">
      {t("reporting.panel.empty")}
    </p>
  {:else}
    <ul class="divide-y divide-border rounded-xl border border-border bg-surface-raised">
      {#each data.reports as report (report.id)}
        <li class="flex items-center gap-3 px-4 py-3">
          <FileText size={16} class="shrink-0 text-text-muted" />
          <a href={`/reports/${report.id}`} class="min-w-0 flex-1 text-sm hover:underline">
            <span class="block truncate text-text">{periodLabel(report, locale)}</span>
            <span class="block text-xs text-text-muted">{audienceLabel(report.audience)}</span>
          </a>
          <ReportStatusPill status={report.status} size="xs" />
        </li>
      {/each}
    </ul>
  {/if}
</section>
