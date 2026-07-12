<script lang="ts">
  /**
   * "Brief me" + monthly report drafts on the company page (#130).
   *
   * The numbers come from the API (panel providers + module tools), the narrative from the
   * model, and a report is always a draft: markdown in the shared editor, saved as a record
   * (auditable, §16), never auto-sent.
   */
  import { FileText, Sparkles } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";
  import Markdown from "$lib/core/ui/Markdown.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import RichTextEditor from "$lib/core/ui/RichTextEditor.svelte";

  import { sourceHref, type AISource } from "./index";
  import { streamAI } from "./stream";

  let { companyId, companyName }: { companyId: string; companyName: string } = $props();

  // --- digest ("brief me") -----------------------------------------------------
  let digestOpen = $state(false);
  let digest = $state("");
  let digestSources = $state<AISource[]>([]);
  let digestBusy = $state(false);
  let digestError = $state<string | null>(null);
  let digestBudget = $state(false);
  let digestAbort: AbortController | null = null;

  async function runDigest(override = false) {
    digest = "";
    digestSources = [];
    digestError = null;
    digestBudget = false;
    digestBusy = true;
    digestAbort = new AbortController();
    try {
      const failure = await streamAI(
        `companies/${companyId}/digest`,
        { override_budget: override },
        {
          onText: (delta) => (digest += delta),
          onSources: (sources) => (digestSources = sources),
          onError: (_code, message) => (digestError = message),
        },
        digestAbort.signal,
      );
      if (failure) {
        if (failure.code === "ai_budget_reached") digestBudget = true;
        else digestError = failure.message;
      }
    } catch (err) {
      if (!(err instanceof DOMException && err.name === "AbortError")) {
        digestError = "errors.ai_provider_error";
      }
    } finally {
      digestBusy = false;
      digestAbort = null;
    }
  }

  function openDigest() {
    digestOpen = true;
    if (!digest && !digestBusy) void runDigest();
  }

  // --- monthly report drafts -----------------------------------------------------
  interface Report {
    id: string;
    period: string;
    language: string;
    title: string;
    content: string;
    updated_at: string;
  }

  let reportOpen = $state(false);
  let reports = $state<Report[]>([]);
  let reportsLoaded = $state(false);
  // null = list view; otherwise the draft being edited ("" id = unsaved generation).
  let draft = $state<{
    id: string;
    period: string;
    language: string;
    title: string;
    content: string;
  } | null>(null);
  let reportBusy = $state(false);
  let reportError = $state<string | null>(null);
  let reportBudget = $state(false);
  let confirmDeleteId = $state<string | null>(null);
  let genPeriod = $state(defaultPeriod());
  let genLanguage = $state("nl");

  function defaultPeriod(): string {
    const d = new Date();
    d.setMonth(d.getMonth() - 1); // reports are written about the month just ended
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
  }

  const periodOptions = $derived.by(() => {
    const options: string[] = [];
    const d = new Date();
    for (let i = 0; i < 12; i++) {
      options.push(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`);
      d.setMonth(d.getMonth() - 1);
    }
    return options;
  });

  function periodLabel(period: string): string {
    const [year, month] = period.split("-").map(Number);
    return new Intl.DateTimeFormat(genLanguage === "en" ? "en-GB" : "nl-NL", {
      month: "long",
      year: "numeric",
    }).format(new Date(Date.UTC(year, month - 1, 1)));
  }

  async function loadReports() {
    const res = await fetch(`/ai/reports?company_id=${companyId}`);
    if (res.ok) reports = await res.json();
    reportsLoaded = true;
  }

  function openReports() {
    reportOpen = true;
    draft = null;
    reportError = null;
    if (!reportsLoaded) void loadReports();
  }

  async function generate(override = false) {
    reportError = null;
    reportBudget = false;
    reportBusy = true;
    draft = {
      id: "",
      period: genPeriod,
      language: genLanguage,
      title: `${t("ai.report.title_prefix")} ${companyName} – ${periodLabel(genPeriod)}`,
      content: "",
    };
    try {
      const failure = await streamAI(
        "reports/generate",
        {
          company_id: companyId,
          period: genPeriod,
          language: genLanguage,
          override_budget: override,
        },
        {
          onText: (delta) => {
            if (draft) draft.content += delta;
          },
          onError: (_code, message) => (reportError = message),
        },
      );
      if (failure) {
        draft = null;
        if (failure.code === "ai_budget_reached") reportBudget = true;
        else reportError = failure.message;
      }
    } catch {
      reportError = "errors.ai_provider_error";
    } finally {
      reportBusy = false;
    }
  }

  async function saveDraft() {
    if (!draft) return;
    reportError = null;
    const isNew = !draft.id;
    const res = await fetch(isNew ? "/ai/reports" : `/ai/reports/${draft.id}`, {
      method: isNew ? "POST" : "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(
        isNew
          ? {
              company_id: companyId,
              period: draft.period,
              language: draft.language,
              title: draft.title,
              content: draft.content,
            }
          : { title: draft.title, content: draft.content },
      ),
    });
    if (!res.ok) {
      const payload = await res.json().catch(() => null);
      reportError = payload?.error?.message ?? "errors.ai_provider_error";
      return;
    }
    await loadReports();
    draft = null;
  }

  async function removeReport(id: string) {
    await fetch(`/ai/reports/${id}`, { method: "DELETE" });
    confirmDeleteId = null;
    await loadReports();
  }

  async function copyDraft() {
    if (draft?.content) await navigator.clipboard.writeText(draft.content);
  }
</script>

<button
  type="button"
  class="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-text-muted hover:border-brand hover:text-brand"
  onclick={openDigest}
>
  <Sparkles size={14} />
  {t("ai.digest.action")}
</button>
<button
  type="button"
  class="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-text-muted hover:border-brand hover:text-brand"
  onclick={openReports}
>
  <FileText size={14} />
  {t("ai.report.action")}
</button>

<Modal bind:open={digestOpen} title={t("ai.digest.title", { name: companyName })}>
  {#if digestError}
    <p class="text-sm text-red-600 dark:text-red-400">{t(digestError)}</p>
  {/if}
  {#if digestBudget}
    <p class="text-sm text-amber-700 dark:text-amber-400">
      {t("ai.budget_notice")}
      <button type="button" class="underline" onclick={() => void runDigest(true)}
        >{t("ai.budget_proceed")}</button
      >
    </p>
  {/if}
  {#if digest}
    <div class="max-h-96 overflow-y-auto text-sm">
      <Markdown value={digest} />
    </div>
  {:else if digestBusy}
    <p class="animate-pulse text-sm text-text-muted">{t("ai.digest.generating")}</p>
  {/if}
  {#if digestSources.length > 0}
    <div class="mt-3 flex flex-wrap gap-1.5 border-t border-border pt-3">
      {#each digestSources as source (source.type + source.id)}
        {@const href = sourceHref(source)}
        {#if href}
          <a
            {href}
            class="rounded-full border border-border px-2 py-0.5 text-xs text-text-muted hover:border-brand hover:text-brand"
            >{source.label || t(`ai.source.${source.type}`)}</a
          >
        {/if}
      {/each}
    </div>
  {/if}
  {#if !digestBusy && digest}
    <div class="mt-3">
      <button
        type="button"
        class="rounded-lg border border-border px-3 py-1.5 text-sm text-text hover:border-brand"
        onclick={() => void runDigest()}>{t("ai.digest.refresh")}</button
      >
    </div>
  {/if}
</Modal>

<Modal bind:open={reportOpen} title={t("ai.report.title", { name: companyName })}>
  {#if reportError}
    <p class="mb-2 text-sm text-red-600 dark:text-red-400">{t(reportError)}</p>
  {/if}
  {#if reportBudget}
    <p class="mb-2 text-sm text-amber-700 dark:text-amber-400">
      {t("ai.budget_notice")}
      <button type="button" class="underline" onclick={() => void generate(true)}
        >{t("ai.budget_proceed")}</button
      >
    </p>
  {/if}

  {#if draft}
    <div class="space-y-3">
      <input
        bind:value={draft.title}
        class="w-full rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand"
        aria-label={t("ai.report.draft_title")}
      />
      {#if reportBusy}
        <div class="max-h-72 overflow-y-auto rounded-lg border border-border px-3 py-2 text-sm">
          <Markdown value={draft.content} />
        </div>
      {:else}
        {#key draft.id || "new"}
          <RichTextEditor
            value={draft.content}
            name={null}
            rows={12}
            onchange={(next) => {
              if (draft) draft.content = next;
            }}
          />
        {/key}
      {/if}
      <div class="flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={reportBusy || !draft.content.trim()}
          class="rounded-lg bg-brand px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40"
          onclick={() => void saveDraft()}>{t("common.save")}</button
        >
        <button
          type="button"
          disabled={reportBusy}
          class="rounded-lg border border-border px-3 py-1.5 text-sm text-text hover:border-brand disabled:opacity-40"
          onclick={() => void copyDraft()}>{t("ai.report.copy")}</button
        >
        <button
          type="button"
          class="rounded-lg px-3 py-1.5 text-sm text-text-muted hover:text-text"
          onclick={() => (draft = null)}>{t("common.cancel")}</button
        >
      </div>
    </div>
  {:else}
    <div class="mb-4 flex flex-wrap items-end gap-2">
      <div>
        <label for="ai-report-period" class="mb-1 block text-xs text-text-muted"
          >{t("ai.report.period")}</label
        >
        <select
          id="ai-report-period"
          bind:value={genPeriod}
          class="rounded-lg border border-border px-2 py-1.5 text-sm"
        >
          {#each periodOptions as option (option)}
            <option value={option}>{periodLabel(option)}</option>
          {/each}
        </select>
      </div>
      <div>
        <label for="ai-report-language" class="mb-1 block text-xs text-text-muted"
          >{t("ai.report.language")}</label
        >
        <select
          id="ai-report-language"
          bind:value={genLanguage}
          class="rounded-lg border border-border px-2 py-1.5 text-sm"
        >
          <option value="nl">{t("locale.nl")}</option>
          <option value="en">{t("locale.en")}</option>
        </select>
      </div>
      <button
        type="button"
        disabled={reportBusy}
        class="flex items-center gap-1.5 rounded-lg bg-brand px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40"
        onclick={() => void generate()}
      >
        <Sparkles size={14} />
        {t("ai.report.generate")}
      </button>
    </div>

    {#if reports.length > 0}
      <h3 class="mb-2 text-xs font-medium uppercase tracking-wide text-text-muted">
        {t("ai.report.drafts")}
      </h3>
      <ul class="space-y-1">
        {#each reports as report (report.id)}
          <li
            class="flex items-center justify-between gap-2 rounded-lg border border-border px-3 py-2"
          >
            <button
              type="button"
              class="min-w-0 flex-1 truncate text-left text-sm text-text hover:text-brand"
              onclick={() =>
                (draft = {
                  id: report.id,
                  period: report.period,
                  language: report.language,
                  title: report.title,
                  content: report.content,
                })}>{report.title}</button
            >
            <button
              type="button"
              class="shrink-0 text-xs text-red-600 hover:underline dark:text-red-400"
              onclick={() => (confirmDeleteId = report.id)}>{t("common.delete")}</button
            >
          </li>
        {/each}
      </ul>
    {:else if reportsLoaded}
      <p class="text-sm text-text-muted">{t("ai.report.empty")}</p>
    {/if}
  {/if}
</Modal>

<!-- ConfirmDialog is form-posting; this delete goes through the AI proxy, so the confirm
     (every delete confirms, docs/UX.md) is a small Modal of its own. -->
{#if confirmDeleteId}
  <Modal open={true} title={t("common.delete")}>
    <p class="text-sm text-text-muted">{t("ai.report.delete_confirm")}</p>
    <div class="mt-5 flex justify-end gap-2">
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm text-text"
        onclick={() => (confirmDeleteId = null)}>{t("common.cancel")}</button
      >
      <button
        type="button"
        class="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        onclick={() => void removeReport(confirmDeleteId!)}>{t("common.delete")}</button
      >
    </div>
  </Modal>
{/if}
