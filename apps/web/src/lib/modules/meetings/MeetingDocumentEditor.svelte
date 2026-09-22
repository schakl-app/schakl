<script lang="ts">
  /**
   * What the minutes document looks like: the design, the header image, the chapters a
   * download ticks by default, the tenant's own Jinja — beside a live preview drawn by the
   * renderer every download comes out of (the report template editor's shape, one document
   * family over).
   *
   * The controls are bound state and the hidden inputs below post them, so a tab the admin never
   * opened still carries what the row already had. The preview is debounced: it renders a real
   * document server-side, and a request per keystroke in the CSS box would be a queue.
   */
  import { t } from "$lib/core/i18n";
  import DocumentFrame from "$lib/core/ui/DocumentFrame.svelte";
  import { filedrop } from "$lib/core/ui/filedrop";

  interface SectionEntry {
    key: string;
    title_key: string;
    default: boolean;
  }

  let {
    sections,
    design = $bindable("standard"),
    customHtml = $bindable<string | null>(null),
    customCss = $bindable<string | null>(null),
    coverFileId = $bindable<string | null>(null),
    tickedSections = $bindable<string[]>([]),
    accentColor = "",
    footerText = "",
    avatars = true,
  }: {
    sections: SectionEntry[];
    design: string;
    customHtml: string | null;
    customCss: string | null;
    coverFileId: string | null;
    tickedSections: string[];
    accentColor: string;
    footerText: string;
    avatars: boolean;
  } = $props();

  type Tab = "design" | "sections" | "source";
  let tab = $state<Tab>("design");
  const TABS: { key: Tab; label: string }[] = $derived([
    { key: "design" as const, label: t("settings.meetings.tab_design") },
    { key: "sections" as const, label: t("settings.meetings.tab_sections") },
    { key: "source" as const, label: t("settings.meetings.tab_source") },
  ]);
  const DESIGNS = ["standard", "custom"] as const;

  function toggleSection(key: string) {
    tickedSections = tickedSections.includes(key)
      ? tickedSections.filter((k) => k !== key)
      : [...tickedSections, key];
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
      const res = await fetch("/settings/meetings/cover", { method: "POST", body });
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
  async function loadSource({ confirmFirst = true } = {}) {
    if (
      confirmFirst &&
      (customHtml ?? "").trim() &&
      !confirm(t("settings.meetings.source_overwrite_confirm"))
    )
      return;
    const res = await fetch("/settings/meetings/source?design=standard");
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

  // --- preview ----------------------------------------------------------------------- #
  let previewHtml = $state("");
  let previewBusy = $state(false);
  let previewError = $state("");

  const serialized = $derived(
    JSON.stringify({
      document_design: design,
      document_custom_html: customHtml,
      document_custom_css: customCss,
      document_accent_color: accentColor.trim() || null,
      document_cover_file_id: coverFileId,
      document_footer_text: footerText.trim() || null,
      document_sections: tickedSections,
      document_avatars: avatars,
    }),
  );

  $effect(() => {
    const payload = serialized;
    const timer = setTimeout(() => void render(payload), 500);
    return () => clearTimeout(timer);
  });

  async function render(payload: string) {
    previewBusy = true;
    try {
      const res = await fetch("/settings/meetings/preview", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: payload,
      });
      if (!res.ok) {
        const detail = res.status === 422 ? await res.json().catch(() => null) : null;
        previewError = detail?.error?.fields?.html ?? t("settings.meetings.preview_failed");
        return;
      }
      previewError = "";
      previewHtml = await res.text();
    } catch {
      previewError = t("settings.meetings.preview_failed");
    } finally {
      previewBusy = false;
    }
  }

  const inputClass =
    "w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";
  const codeClass = `${inputClass} font-mono text-xs leading-relaxed`;
</script>

<!-- The values the surrounding form posts. Bound state above them, so a tab the admin never
     opened still carries what the row already had (absent is not clear). -->
<input type="hidden" name="document_design" value={design} />
<input type="hidden" name="document_custom_html" value={customHtml ?? ""} />
<input type="hidden" name="document_custom_css" value={customCss ?? ""} />
<input type="hidden" name="document_cover_file_id" value={coverFileId ?? ""} />
{#each tickedSections as key (key)}
  <input type="hidden" name="document_sections" value={key} />
{/each}

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
        <legend class="mb-1 text-sm font-medium text-text">{t("settings.meetings.design")}</legend>
        <div class="space-y-1">
          {#each DESIGNS as option (option)}
            <label class="flex items-center gap-2 text-sm text-text">
              <input
                type="radio"
                name="design-choice"
                checked={design === option}
                onchange={() => chooseDesign(option)}
                class="border-border"
              />
              {t(`settings.meetings.design.${option}`)}
            </label>
          {/each}
        </div>
        <p class="mt-1 text-xs text-text-muted">{t("settings.meetings.design_hint")}</p>
      </fieldset>

      <fieldset class="space-y-2 rounded-lg border border-border p-3">
        <legend class="px-1 text-sm font-medium text-text">
          {t("settings.meetings.cover_image")}
        </legend>
        <p class="text-xs text-text-muted">{t("settings.meetings.cover_image_hint")}</p>
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
            aria-label={t("settings.meetings.cover_image")}
          />
          <span class="text-xs text-text-muted">{t("common.drop_hint")}</span>
          {#if coverFileId}
            <span class="text-xs text-text-muted"
              >{coverName || t("settings.meetings.cover_set")}</span
            >
            <button
              type="button"
              class="text-xs text-text-muted underline"
              onclick={() => {
                coverFileId = null;
                coverName = "";
              }}>{t("settings.meetings.cover_clear")}</button
            >
          {/if}
        </div>
        {#if uploadError}
          <p class="text-xs text-red-600 dark:text-red-400">{uploadError}</p>
        {/if}
      </fieldset>
    {:else if tab === "sections"}
      <p class="text-xs text-text-muted">{t("settings.meetings.sections_hint")}</p>
      <ul class="divide-y divide-border rounded-lg border border-border">
        {#each sections as section (section.key)}
          <li class="px-3 py-2">
            <label class="flex items-center gap-2 text-sm text-text">
              <input
                type="checkbox"
                checked={tickedSections.includes(section.key)}
                onchange={() => toggleSection(section.key)}
                class="rounded border-border"
              />
              <span class={tickedSections.includes(section.key) ? "" : "text-text-muted"}>
                {t(section.title_key)}
              </span>
            </label>
          </li>
        {/each}
      </ul>
    {:else}
      <p class="text-xs text-text-muted">{t("settings.meetings.source_hint")}</p>
      <button
        type="button"
        class="rounded-lg border border-border px-3 py-2 text-sm text-text hover:bg-surface"
        onclick={() => loadSource()}>{t("settings.meetings.source_load")}</button
      >
      <div>
        <label for="mtg-html" class="mb-1 block text-sm font-medium text-text">
          {t("settings.meetings.source_html")}
        </label>
        <textarea
          id="mtg-html"
          rows="16"
          value={customHtml ?? ""}
          oninput={(e) => (customHtml = e.currentTarget.value)}
          spellcheck="false"
          class={codeClass}></textarea>
      </div>
      <div>
        <label for="mtg-css" class="mb-1 block text-sm font-medium text-text">
          {t("settings.meetings.source_css")}
        </label>
        <textarea
          id="mtg-css"
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
      <p class="text-sm font-medium text-text">{t("settings.meetings.preview")}</p>
      {#if previewError}
        <p class="text-xs text-red-600 dark:text-red-400">{previewError}</p>
      {/if}
    </div>
    <div class="max-h-[70vh] overflow-y-auto rounded-lg border border-border bg-surface">
      <DocumentFrame
        srcdoc={previewHtml}
        loading={previewBusy && !previewHtml}
        title={t("settings.meetings.preview")}
      />
    </div>
    <p class="mt-2 text-xs text-text-muted">{t("settings.meetings.preview_hint")}</p>
  </div>
</div>
