<script lang="ts">
  /**
   * What a report looks like: the design, the accent, the photograph on the cover, the intro
   * paragraph, which sections print — and, on its own tab, the design's own HTML and CSS.
   *
   * `report_templates` has carried `design`, `custom_html`, `custom_css` and
   * `cover_image_file_id` since #300 and this is the screen that finally reaches them. The
   * shape is invoicing's `TemplateEditor`, which `docs/REPORTING.md` already named as the one
   * this wanted; what is new is that the *preview* renders the tenant's own most recent report
   * rather than a fixture, so an author is looking at the page their client will actually get.
   *
   * The preview is the API's real renderer working on an unsaved config — the only honest
   * kind, because it is the very page WeasyPrint will print. That costs a round trip per edit,
   * so it is debounced and never blocks typing.
   *
   * Everything here is bound state that the surrounding `<form>` posts as hidden fields. The
   * component draws no submit button of its own: a template is saved by the form it lives in,
   * beside its name and audience.
   */
  import type { SvelteSet } from "svelte/reactivity";

  import { t } from "$lib/core/i18n";
  import DocumentFrame from "$lib/core/ui/DocumentFrame.svelte";
  import { filedrop } from "$lib/core/ui/filedrop";

  import type { ReportTemplate, SectionCatalogEntry } from "./types";

  let {
    template,
    sections,
    audience,
    design = $bindable(),
    customHtml = $bindable(),
    customCss = $bindable(),
    coverFileId = $bindable(),
    accentColor,
    introText,
    disabledSections,
  }: {
    template: ReportTemplate;
    /** The catalog for this template's audience — what it may switch off. */
    sections: SectionCatalogEntry[];
    audience: string;
    design: string;
    customHtml: string | null;
    customCss: string | null;
    coverFileId: string | null;
    /** Read-only here: the accent and intro are plain inputs on the surrounding form, and the
     *  preview needs their current values without owning them. */
    accentColor: string;
    introText: string;
    /** Reactive by identity, so it is mutated in place rather than bound and replaced — which
     *  is also what keeps the parent's hidden `layout` field recomputing as boxes are ticked. */
    disabledSections: SvelteSet<string>;
  } = $props();

  type Tab = "design" | "sections" | "source";
  let tab = $state<Tab>("design");

  const TABS: { key: Tab; label: string }[] = $derived([
    { key: "design" as const, label: t("settings.reporting.tab_design") },
    { key: "sections" as const, label: t("settings.reporting.tab_sections") },
    { key: "source" as const, label: t("settings.reporting.tab_source") },
  ]);

  /** `custom` is never a *shipped* design — it is the tenant's own body, rendered in the shell. */
  const DESIGNS = ["standard", "custom"] as const;

  function toggleSection(key: string) {
    if (disabledSections.has(key)) disabledSections.delete(key);
    else disabledSections.add(key);
  }

  // --- cover ------------------------------------------------------------------------- #
  let uploading = $state(false);
  let uploadError = $state("");
  let coverName = $state("");

  async function uploadCover(event: Event) {
    const input = event.currentTarget as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    uploading = true;
    uploadError = "";
    try {
      const body = new FormData();
      body.append("file", file, file.name);
      const res = await fetch("/settings/reporting/cover", { method: "POST", body });
      if (!res.ok) throw new Error(String(res.status));
      const meta = (await res.json()) as { id: string };
      coverFileId = meta.id;
      coverName = file.name;
    } catch {
      uploadError = t("errors.upload_type");
    } finally {
      uploading = false;
      input.value = "";
    }
  }

  // --- source ------------------------------------------------------------------------ #
  /**
   * Picking "eigen HTML" with nothing written yet starts from the shipped design.
   *
   * An empty custom body falls back to `standard` at render time and refuses to be *useful*,
   * so without this the radio button puts the editor into a state whose only feedback is a
   * preview that looks unchanged — and the fix, on a tab they may not have opened, is not
   * discoverable. Invoicing learned this one first.
   */
  async function loadSource({ confirmFirst = true } = {}) {
    if (
      confirmFirst &&
      (customHtml ?? "").trim() &&
      !confirm(t("settings.reporting.source_overwrite_confirm"))
    )
      return;
    const res = await fetch("/settings/reporting/source?design=standard");
    if (!res.ok) return;
    const body = (await res.json()) as { html: string; css: string };
    customHtml = body.html;
    customCss = body.css;
    design = "custom";
  }

  async function chooseDesign(next: string) {
    if (next !== "custom") {
      design = next;
      return;
    }
    if ((customHtml ?? "").trim()) {
      design = "custom";
      return;
    }
    await loadSource({ confirmFirst: false });
    tab = "source";
  }

  // --- live preview ------------------------------------------------------------------- #
  let previewHtml = $state("");
  let previewBusy = $state(false);
  let previewError = $state("");

  const serialized = $derived(
    JSON.stringify({
      audience,
      design,
      custom_html: customHtml,
      custom_css: customCss,
      accent_color: accentColor.trim() || null,
      cover_image_file_id: coverFileId,
      intro_text: introText.trim() || null,
    }),
  );

  $effect(() => {
    // Debounced rather than per-keystroke: this renders a real document server-side, and a
    // request per character in the CSS box would be a queue, not a preview.
    const payload = serialized;
    const timer = setTimeout(() => void render(payload), 500);
    return () => clearTimeout(timer);
  });

  async function render(payload: string) {
    previewBusy = true;
    try {
      const res = await fetch("/settings/reporting/preview", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: payload,
      });
      if (!res.ok) {
        // A custom body's own Jinja error comes back as a 422 envelope naming the field;
        // showing it beats "kon niet worden weergegeven" when the author is looking at code.
        const detail = res.status === 422 ? await res.json().catch(() => null) : null;
        previewError = detail?.error?.fields?.html ?? t("settings.reporting.preview_failed");
        return;
      }
      previewError = "";
      previewHtml = await res.text();
    } catch {
      previewError = t("settings.reporting.preview_failed");
    } finally {
      previewBusy = false;
    }
  }

  const inputClass =
    "w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";
  const codeClass = `${inputClass} font-mono text-xs leading-relaxed`;
</script>

<!-- The values the surrounding form posts. The controls above them are bound state, so a tab
     the author never opened still carries what the template already had (#300's "absent is not
     clear": only a control the user actually saw may empty a field). -->
<input type="hidden" name="design" value={design} />
<input type="hidden" name="custom_html" value={customHtml ?? ""} />
<input type="hidden" name="custom_css" value={customCss ?? ""} />
<input type="hidden" name="cover_image_file_id" value={coverFileId ?? ""} />

<!-- Controls left, document right, and the document gets real width: a preview you cannot read
     is a thumbnail, and the point of rendering the actual page is that the author can judge it.
     The Code tab is why the controls still take the larger share — Jinja in a 22rem box wraps
     every line. -->
<div class="grid min-w-0 gap-6 lg:grid-cols-[minmax(0,1fr)_28rem]">
  <div class="min-w-0 space-y-4">
    <div class="flex gap-1 border-b border-border" role="tablist">
      {#each TABS as entry (entry.key)}
        <button
          type="button"
          role="tab"
          aria-selected={tab === entry.key}
          class="-mb-px border-b-2 px-3 py-2 text-sm {tab === entry.key
            ? 'border-brand font-medium text-text'
            : 'border-transparent text-text-muted hover:text-text'}"
          onclick={() => (tab = entry.key)}>{entry.label}</button
        >
      {/each}
    </div>

    {#if tab === "design"}
      <fieldset>
        <legend class="mb-1 text-sm font-medium text-text">
          {t("settings.reporting.design")}
        </legend>
        <div class="space-y-1">
          {#each DESIGNS as option (option)}
            <label class="flex items-center gap-2 text-sm text-text">
              <input
                type="radio"
                name="design-choice-{template.id || 'new'}"
                checked={design === option}
                onchange={() => chooseDesign(option)}
                class="border-border"
              />
              {t(`settings.reporting.design.${option}`)}
            </label>
          {/each}
        </div>
        <p class="mt-1 text-xs text-text-muted">{t("settings.reporting.design_hint")}</p>
      </fieldset>

      <fieldset class="space-y-2 rounded-lg border border-border p-3">
        <legend class="px-1 text-sm font-medium text-text">
          {t("settings.reporting.cover_image")}
        </legend>
        <p class="text-xs text-text-muted">{t("settings.reporting.cover_image_hint")}</p>
        <div
          class="flex flex-wrap items-center gap-2"
          use:filedrop={{ disabled: uploading, onerror: (key) => (uploadError = t(key)) }}
        >
          <input
            type="file"
            accept="image/*"
            onchange={uploadCover}
            disabled={uploading}
            class="text-xs text-text-muted"
            aria-label={t("settings.reporting.cover_image")}
          />
          <span class="text-xs text-text-muted">{t("common.drop_hint")}</span>
          {#if coverFileId}
            <span class="text-xs text-text-muted"
              >{coverName || t("settings.reporting.cover_set")}</span
            >
            <button
              type="button"
              class="text-xs text-text-muted underline"
              onclick={() => {
                coverFileId = null;
                coverName = "";
              }}>{t("settings.reporting.cover_clear")}</button
            >
          {/if}
        </div>
        {#if uploadError}
          <p class="text-xs text-red-600 dark:text-red-400">{uploadError}</p>
        {/if}
      </fieldset>
    {:else if tab === "sections"}
      <p class="text-xs text-text-muted">{t("settings.reporting.sections_hint")}</p>
      <ul class="divide-y divide-border rounded-lg border border-border">
        {#each sections as section (section.key)}
          <li class="px-3 py-2">
            <label class="flex items-center gap-2 text-sm text-text">
              <input
                type="checkbox"
                checked={!disabledSections.has(section.key)}
                onchange={() => toggleSection(section.key)}
                class="rounded border-border"
              />
              <span class={disabledSections.has(section.key) ? "text-text-muted" : ""}>
                {t(section.title_key)}
              </span>
            </label>
          </li>
        {/each}
      </ul>
    {:else}
      <p class="text-xs text-text-muted">{t("settings.reporting.source_hint")}</p>
      <button
        type="button"
        class="rounded-lg border border-border px-3 py-2 text-sm text-text hover:bg-surface"
        onclick={() => loadSource()}>{t("settings.reporting.source_load")}</button
      >
      <div>
        <label
          for="rpt-html-{template.id || 'new'}"
          class="mb-1 block text-sm font-medium text-text"
        >
          {t("settings.reporting.source_html")}
        </label>
        <textarea
          id="rpt-html-{template.id || 'new'}"
          rows="16"
          value={customHtml ?? ""}
          oninput={(e) => (customHtml = e.currentTarget.value)}
          spellcheck="false"
          class={codeClass}></textarea>
      </div>
      <div>
        <label
          for="rpt-css-{template.id || 'new'}"
          class="mb-1 block text-sm font-medium text-text"
        >
          {t("settings.reporting.source_css")}
        </label>
        <textarea
          id="rpt-css-{template.id || 'new'}"
          rows="16"
          value={customCss ?? ""}
          oninput={(e) => (customCss = e.currentTarget.value)}
          spellcheck="false"
          class={codeClass}></textarea>
      </div>
    {/if}
  </div>

  <div class="min-w-0">
    <div class="mb-2 flex items-center justify-between gap-2">
      <p class="text-sm font-medium text-text">{t("settings.reporting.preview")}</p>
      {#if previewError}
        <p class="text-xs text-red-600 dark:text-red-400">{previewError}</p>
      {/if}
    </div>
    <div class="max-h-[70vh] overflow-y-auto rounded-lg border border-border bg-surface">
      <DocumentFrame
        srcdoc={previewHtml}
        loading={previewBusy && !previewHtml}
        title={t("settings.reporting.preview")}
      />
    </div>
    <p class="mt-2 text-xs text-text-muted">{t("settings.reporting.preview_hint")}</p>
  </div>
</div>
