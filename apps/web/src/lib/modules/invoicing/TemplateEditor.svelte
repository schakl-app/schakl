<script lang="ts">
  /**
   * The template editor: which design, which blocks in which order, which fields, what mark
   * behind the page — and, for whoever holds the permission, the design's own HTML and CSS.
   *
   * The preview beside it is the API's real renderer working on an unsaved config, which is
   * the only honest kind: it is the very page that will be printed, so a design cannot look
   * one way here and another in the client's inbox. That costs a round trip per edit, so it
   * is debounced and it never blocks typing — the old preview lags behind by a moment rather
   * than the field waiting for it.
   *
   * The layout is a **partial statement** (see `render/blocks.py`): the editor sends every
   * block and field it knows about, and the API resolves anything it does not against the
   * catalog. That is what lets a field added by a later release appear on a template laid out
   * today, instead of being silently absent because this editor never named it.
   */
  import { ChevronDown, ChevronUp, Lock } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";
  import { editLocale } from "$lib/core/i18n-edit.svelte";
  import I18nLocaleSwitcher from "$lib/core/ui/I18nLocaleSwitcher.svelte";

  import DocumentFrame from "$lib/core/ui/DocumentFrame.svelte";
  import type { BlockSpec, TemplateConfig, TemplateLayoutBlock } from "./templateConfig";
  import { DEFAULT_CONFIG, layoutForApi, mergeLayout, moveItem } from "./templateConfig";

  let {
    config = $bindable(),
    catalog,
    canAuthor = false,
    templateId = null,
  }: {
    config: TemplateConfig;
    catalog: BlockSpec[];
    canAuthor?: boolean;
    /** The template being edited, if it is saved. Lets the preview treat its *stored* code
     *  as the baseline, so redrawing a custom design needs no authoring permission. */
    templateId?: string | null;
  } = $props();

  type Tab = "design" | "layout" | "texts" | "source";
  let tab = $state<Tab>("design");
  /** Which block's field list is unfolded. One at a time — the list is long enough already. */
  let openBlock = $state<string | null>(null);
  /** Which language the tenant's own wording — the layout tab's label boxes and the texts tab —
   *  is being written in. The surface's single `I18nLocaleSwitcher`, shared with every other
   *  translation editor in the app (docs/UX.md), not a switch per box. */
  const labelLocale = $derived(editLocale.current);

  const TABS: { key: Tab; label: string }[] = $derived([
    { key: "design" as const, label: t("settings.invoicing.tab_design") },
    { key: "layout" as const, label: t("settings.invoicing.tab_layout") },
    { key: "texts" as const, label: t("settings.invoicing.tab_texts") },
    ...(canAuthor ? [{ key: "source" as const, label: t("settings.invoicing.tab_source") }] : []),
  ]);

  const DESIGNS = ["classic", "letterhead", "custom"] as const;
  const catalogByKey = $derived(new Map(catalog.map((block) => [block.key, block])));

  /** The stored layout merged onto the catalog — what the list draws and what gets sent. */
  const layout = $derived(mergeLayout(config.layout ?? [], catalog));

  function blockLabel(key: string) {
    return t(`invoicing.block.${key}`);
  }
  function fieldLabel(block: string, key: string) {
    return t(`invoicing.field.${block}.${key}`);
  }

  // Stored in the shape the API accepts, never the shape the list draws in: `locked` and
  // `region` are the editor's own bookkeeping, re-read from the catalog by `mergeLayout` on
  // the way out and rejected as extra keys on the way in.
  function writeLayout(next: TemplateLayoutBlock[]) {
    config = { ...config, layout: layoutForApi(next) };
  }

  function toggleBlock(key: string) {
    writeLayout(
      layout.map((block) =>
        block.key === key && !block.locked ? { ...block, enabled: !block.enabled } : block,
      ),
    );
  }

  function toggleField(blockKey: string, fieldKey: string) {
    writeLayout(
      layout.map((block) =>
        block.key !== blockKey
          ? block
          : {
              ...block,
              fields: block.fields.map((field) =>
                field.key === fieldKey && !field.locked
                  ? { ...field, enabled: !field.enabled }
                  : field,
              ),
            },
      ),
    );
  }

  /**
   * Reordering is bounded to the block's own region. A design decides *where* the sender
   * block sits; only the body is genuinely a stack, so only the body reorders — offering to
   * move `seller` below `totals` would promise a document no design can draw.
   */
  function moveBlock(key: string, delta: -1 | 1) {
    const index = layout.findIndex((block) => block.key === key);
    const region = layout[index]?.region;
    const siblings = layout.filter((block) => block.region === region);
    const within = siblings.findIndex((block) => block.key === key);
    const target = siblings[within + delta];
    if (!target) return;
    writeLayout(
      moveItem(
        layout,
        index,
        layout.findIndex((b) => b.key === target.key),
      ),
    );
  }

  /**
   * The tenant's own wording for a field's printed label — "t" where we say "Telefoon".
   *
   * Per locale, because the document prints in *its* language, not the editor's: a Dutch
   * invoice and an English one to the same client want different words for the same field.
   * Blank is not a label; it clears the override so the catalog's wording comes back, which
   * is also what stops an empty column heading from being saveable.
   */
  function setFieldLabel(blockKey: string, fieldKey: string, text: string) {
    writeLayout(
      layout.map((block) => {
        if (block.key !== blockKey) return block;
        return {
          ...block,
          fields: block.fields.map((field) => {
            if (field.key !== fieldKey) return field;
            const labels = { ...(field.label_i18n ?? {}) };
            if (text.trim()) labels[labelLocale] = text;
            else delete labels[labelLocale];
            return { ...field, label_i18n: labels };
          }),
        };
      }),
    );
  }

  function moveField(blockKey: string, fieldKey: string, delta: -1 | 1) {
    writeLayout(
      layout.map((block) => {
        if (block.key !== blockKey) return block;
        const index = block.fields.findIndex((field) => field.key === fieldKey);
        if (index + delta < 0 || index + delta >= block.fields.length) return block;
        return { ...block, fields: moveItem(block.fields, index, index + delta) };
      }),
    );
  }

  function resetLayout() {
    config = { ...config, layout: [] };
  }

  function movable(key: string) {
    return catalogByKey.get(key)?.movable ?? false;
  }

  // --- background ------------------------------------------------------------------- #
  const background = $derived(config.background ?? DEFAULT_CONFIG.background);
  function setBackground(patch: Partial<typeof background>) {
    config = { ...config, background: { ...background, ...patch } };
  }

  let uploading = $state(false);
  let uploadError = $state("");
  async function uploadBackground(event: Event) {
    const input = event.currentTarget as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    uploading = true;
    uploadError = "";
    try {
      const body = new FormData();
      body.append("file", file, file.name);
      const res = await fetch("/settings/invoicing/background", { method: "POST", body });
      if (!res.ok) throw new Error(String(res.status));
      const meta = (await res.json()) as { id: string };
      setBackground({ file_id: meta.id, enabled: true, use_logo: false });
    } catch {
      uploadError = t("errors.upload_type");
    } finally {
      uploading = false;
      input.value = "";
    }
  }

  // --- the payment QR (#305) ---------------------------------------------------------- #
  //
  // The colour fields exist because the *renderer* holds the guarantee, not because the risk
  // went away. #269 shipped no picker on the reasoning that "letting a tenant type a hex here
  // would be offering them a way to print an invoice nobody's phone can read" — right about
  // the danger, wrong about the remedy. `readable_pair` already replaced an unscannable
  // accent; what was missing was not a locked field, it was a preview that shows the
  // substitution and a sentence that explains it. Both are below.
  const qrStyle = $derived(config.qr_style ?? "brand");
  const qrCaption = $derived(config.qr_caption_i18n ?? {});

  function setQr(patch: Partial<TemplateConfig>) {
    config = { ...config, ...patch };
  }

  function setQrCaption(text: string) {
    const next = { ...qrCaption };
    if (text.trim()) next[labelLocale] = text;
    else delete next[labelLocale];
    setQr({ qr_caption_i18n: next });
  }

  async function uploadQrLogo(event: Event) {
    const input = event.currentTarget as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    uploading = true;
    uploadError = "";
    try {
      const body = new FormData();
      body.append("file", file, file.name);
      // The background's own endpoint: same storage, same `entity_type`, same reason it is not
      // the anonymously-served `branding` type. A second route would be a second copy of that
      // decision.
      const res = await fetch("/settings/invoicing/background", { method: "POST", body });
      if (!res.ok) throw new Error(String(res.status));
      const meta = (await res.json()) as { id: string };
      setQr({ qr_logo_file_id: meta.id, qr_logo: "custom" });
    } catch {
      uploadError = t("errors.upload_type");
    } finally {
      uploading = false;
      input.value = "";
    }
  }

  /**
   * The code itself, drawn by the API from the unsaved config.
   *
   * Its own request rather than a crop of the document preview, for two reasons: a whole-page
   * render is far too slow to drag a colour picker through, and the thing a tenant needs to
   * see here — whether the pair was substituted — is invisible at 3cm inside a scaled A4.
   *
   * Keyed on only the fields that change the code, so typing in the footer text box does not
   * redraw a QR that cannot have changed.
   */
  let qrSvg = $state("");
  let qrReplaced = $state(false);
  const qrKey = $derived(
    JSON.stringify([
      config.qr_style,
      config.qr_color,
      config.qr_background,
      config.qr_logo,
      config.qr_logo_file_id,
      config.accent_color,
    ]),
  );

  $effect(() => {
    const key = qrKey;
    const timer = setTimeout(() => void renderQr(key), 250);
    return () => clearTimeout(timer);
  });

  async function renderQr(_key: string) {
    try {
      const res = await fetch("/settings/invoicing/qr-preview", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ config, template_id: templateId }),
      });
      if (!res.ok) return;
      const body = (await res.json()) as { svg: string; replaced: boolean };
      qrSvg = body.svg;
      qrReplaced = body.replaced;
    } catch {
      // A preview that fails to load is not worth an error line: the document preview beside
      // it already reports a dead API, and this one redraws on the next edit.
    }
  }

  // --- source ----------------------------------------------------------------------- #
  let sourceFrom = $state<"classic" | "letterhead">("classic");
  async function loadSource(design = sourceFrom, { confirmFirst = true } = {}) {
    if (
      confirmFirst &&
      (config.html ?? "").trim() &&
      !confirm(t("settings.invoicing.source_overwrite_confirm"))
    )
      return;
    const res = await fetch(`/settings/invoicing/source?design=${design}`);
    if (!res.ok) return;
    const body = (await res.json()) as { html: string; css: string };
    config = { ...config, html: body.html, css: body.css, design: "custom" };
  }

  /**
   * Picking "Custom HTML" with nothing written yet starts from the design already selected.
   *
   * An empty custom template is refused by the API (it would print a blank page), so without
   * this the radio button puts the editor into a state whose only feedback is the preview
   * failing — and the fix, on a tab they may not have opened, is not discoverable.
   */
  async function chooseDesign(design: TemplateConfig["design"]) {
    if (design !== "custom") {
      config = { ...config, design };
      return;
    }
    const from = config.design === "letterhead" ? "letterhead" : "classic";
    sourceFrom = from;
    if ((config.html ?? "").trim()) {
      config = { ...config, design };
      return;
    }
    await loadSource(from, { confirmFirst: false });
    tab = "source";
  }

  // --- live preview ------------------------------------------------------------------ #
  let previewHtml = $state("");
  let previewBusy = $state(false);
  let previewError = $state("");
  const serialized = $derived(JSON.stringify({ config, template_id: templateId }));

  $effect(() => {
    // Debounced rather than per-keystroke: this renders a real document server-side, and a
    // request per character in the CSS box would be a queue, not a preview.
    const payload = serialized;
    const timer = setTimeout(() => void render(payload), 400);
    return () => clearTimeout(timer);
  });

  async function render(payload: string) {
    previewBusy = true;
    try {
      const res = await fetch("/settings/invoicing/preview", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: payload,
      });
      if (!res.ok) {
        // A custom template's own Jinja error comes back as a 422 envelope naming the field;
        // showing it beats "could not be rendered" when the author is looking at the code.
        const detail = res.status === 422 ? await res.json().catch(() => null) : null;
        previewError = detail?.error?.fields?.html ?? t("settings.invoicing.preview_failed");
        return;
      }
      previewError = "";
      previewHtml = await res.text();
    } catch {
      previewError = t("settings.invoicing.preview_failed");
    } finally {
      previewBusy = false;
    }
  }

  const inputClass =
    "w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
  const codeClass = `${inputClass} font-mono text-xs leading-relaxed`;
  const iconButton =
    "rounded p-1 text-text-muted hover:bg-surface hover:text-text disabled:opacity-30 disabled:hover:bg-transparent";
</script>

<div class="grid min-w-0 gap-6 lg:grid-cols-[24rem_1fr]">
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

    <!-- One switcher for every box the tenant types their own wording into — the field labels on
         the layout tab, the three text blocks, and (since #305) the QR's caption on the design
         tab (docs/UX.md). The source tab carries none. -->
    {#if tab === "layout" || tab === "texts" || tab === "design"}
      <I18nLocaleSwitcher hint={false} />
    {/if}

    {#if tab === "design"}
      <fieldset>
        <legend class="mb-1 text-sm font-medium text-text">
          {t("settings.invoicing.design")}
        </legend>
        <div class="space-y-1">
          {#each DESIGNS as design (design)}
            {#if design !== "custom" || canAuthor}
              <label class="flex items-center gap-2 text-sm text-text">
                <input
                  type="radio"
                  name="design"
                  value={design}
                  checked={config.design === design}
                  onchange={() => chooseDesign(design)}
                  class="border-border"
                />
                {t(`settings.invoicing.design.${design}`)}
              </label>
            {/if}
          {/each}
        </div>
        <p class="mt-1 text-xs text-text-muted">{t("settings.invoicing.design_hint")}</p>
      </fieldset>

      <div>
        <label for="tpl-accent" class="mb-1 block text-sm font-medium text-text">
          {t("settings.invoicing.accent_color")}
        </label>
        <input
          id="tpl-accent"
          value={config.accent_color ?? ""}
          oninput={(e) =>
            (config = { ...config, accent_color: e.currentTarget.value.trim() || null })}
          placeholder="#4f46e5"
          class={inputClass}
        />
        <p class="mt-1 text-xs text-text-muted">{t("settings.invoicing.accent_color_hint")}</p>
      </div>

      <!-- The payment QR (epic #269, opened up in #305). The preview is not decoration: it is
           where `readable_pair`'s substitution becomes visible, which is the only reason a
           colour picker can be offered here at all. -->
      <fieldset class="space-y-3 rounded-lg border border-border p-3">
        <legend class="px-1 text-sm font-medium text-text">
          {t("settings.invoicing.qr_style")}
        </legend>

        <div class="flex flex-wrap gap-4">
          {#each ["brand", "plain", "custom"] as const as style (style)}
            <label class="flex items-center gap-2 text-sm text-text">
              <input
                type="radio"
                name="qr-style"
                checked={qrStyle === style}
                onchange={() => setQr({ qr_style: style })}
                class="border-border"
              />
              {t(`settings.invoicing.qr_style_${style}`)}
            </label>
          {/each}
        </div>
        <p class="text-xs text-text-muted">{t("settings.invoicing.qr_style_help")}</p>

        <div class="flex gap-4">
          <!-- The code, at roughly the size it prints. Sitting beside the controls rather than
               under them, so a colour change and its result are in one glance. -->
          <div
            class="flex h-24 w-24 shrink-0 items-center justify-center rounded-lg border border-border bg-white p-1"
          >
            <!-- eslint-disable-next-line svelte/no-at-html-tags -->
            {@html qrSvg}
          </div>

          <div class="min-w-0 flex-1 space-y-2">
            {#if qrReplaced}
              <!-- The rule, in words. Without this the tenant picks a colour, sees a different
                   one, and concludes the field is broken — which is exactly why #269 preferred
                   to have no field rather than a silent one. -->
              <p class="text-xs text-amber-700 dark:text-amber-400">
                {t("settings.invoicing.qr_unreadable")}
              </p>
            {:else}
              <p class="text-xs text-text-muted">{t("settings.invoicing.qr_preview_hint")}</p>
            {/if}
          </div>
        </div>

        {#if qrStyle === "custom"}
          <div class="grid grid-cols-2 gap-3">
            <div>
              <label for="qr-color" class="mb-1 block text-xs font-medium text-text">
                {t("settings.invoicing.qr_color")}
              </label>
              <div class="flex items-center gap-2">
                <input
                  id="qr-color"
                  type="color"
                  value={config.qr_color ?? config.accent_color ?? "#000000"}
                  oninput={(e) => setQr({ qr_color: e.currentTarget.value })}
                  class="h-9 w-9 shrink-0 cursor-pointer rounded border border-border bg-transparent"
                />
                <!-- The hex box beside the swatch, not instead of it: a brand colour arrives
                     as a hex from a style guide and is pasted, never eyeballed. -->
                <input
                  value={config.qr_color ?? ""}
                  oninput={(e) => setQr({ qr_color: e.currentTarget.value.trim() || null })}
                  placeholder={config.accent_color ?? "#000000"}
                  class={inputClass}
                />
              </div>
            </div>
            <div>
              <label for="qr-bg" class="mb-1 block text-xs font-medium text-text">
                {t("settings.invoicing.qr_background")}
              </label>
              <div class="flex items-center gap-2">
                <input
                  id="qr-bg"
                  type="color"
                  value={config.qr_background ?? "#ffffff"}
                  oninput={(e) => setQr({ qr_background: e.currentTarget.value })}
                  class="h-9 w-9 shrink-0 cursor-pointer rounded border border-border bg-transparent"
                />
                <input
                  value={config.qr_background ?? ""}
                  oninput={(e) => setQr({ qr_background: e.currentTarget.value.trim() || null })}
                  placeholder="#ffffff"
                  class={inputClass}
                />
              </div>
            </div>
          </div>

          <div>
            <span class="mb-1 block text-xs font-medium text-text">
              {t("settings.invoicing.qr_logo")}
            </span>
            <div class="flex flex-wrap gap-4">
              {#each ["brand", "none", "custom"] as const as choice (choice)}
                <label class="flex items-center gap-2 text-sm text-text">
                  <input
                    type="radio"
                    name="qr-logo"
                    checked={(config.qr_logo ?? "brand") === choice}
                    onchange={() => setQr({ qr_logo: choice })}
                    class="border-border"
                  />
                  {t(`settings.invoicing.qr_logo_${choice}`)}
                </label>
              {/each}
            </div>
            <p class="mt-1 text-xs text-text-muted">{t("settings.invoicing.qr_logo_help")}</p>
            {#if config.qr_logo === "custom"}
              <div class="mt-2 flex items-center gap-2">
                <input
                  type="file"
                  accept="image/*"
                  onchange={uploadQrLogo}
                  disabled={uploading}
                  class="text-xs text-text-muted file:mr-2 file:rounded file:border-0 file:bg-surface file:px-2 file:py-1 file:text-xs file:text-text"
                />
                {#if config.qr_logo_file_id}
                  <button
                    type="button"
                    class="text-xs text-text-muted underline"
                    onclick={() => setQr({ qr_logo_file_id: null })}
                  >
                    {t("common.remove")}
                  </button>
                {/if}
              </div>
              {#if uploadError}
                <p class="mt-1 text-xs text-red-600 dark:text-red-400">{uploadError}</p>
              {/if}
            {/if}
          </div>
        {/if}

        <!-- The caption is offered for every style, not only `custom`: rewording the sentence
             under the code is a copy decision, not a colour one, and a tenant who wants
             "Betaal met iDEAL" there should not have to opt into a colour picker for it. -->
        <div>
          <label for="qr-caption" class="mb-1 block text-xs font-medium text-text">
            {t("settings.invoicing.qr_caption")}
          </label>
          <input
            id="qr-caption"
            value={qrCaption[labelLocale] ?? ""}
            oninput={(e) => setQrCaption(e.currentTarget.value)}
            placeholder={t("settings.invoicing.qr_caption_placeholder")}
            class={inputClass}
          />
          <p class="mt-1 text-xs text-text-muted">{t("settings.invoicing.qr_caption_help")}</p>
        </div>
      </fieldset>

      <fieldset class="space-y-2 rounded-lg border border-border p-3">
        <legend class="px-1 text-sm font-medium text-text">
          {t("settings.invoicing.background_heading")}
        </legend>
        <p class="text-xs text-text-muted">{t("settings.invoicing.background_hint")}</p>
        <label class="flex items-center gap-2 text-sm text-text">
          <input
            type="checkbox"
            checked={background.enabled}
            onchange={(e) => setBackground({ enabled: e.currentTarget.checked })}
            class="rounded border-border"
          />
          {t("settings.invoicing.background_enabled")}
        </label>
        {#if background.enabled}
          <label class="flex items-center gap-2 text-sm text-text">
            <input
              type="checkbox"
              checked={background.use_logo && !background.file_id}
              onchange={(e) =>
                setBackground({
                  use_logo: e.currentTarget.checked,
                  file_id: e.currentTarget.checked ? null : background.file_id,
                })}
              class="rounded border-border"
            />
            {t("settings.invoicing.background_use_logo")}
          </label>
          {#if !background.use_logo || background.file_id}
            <div class="flex items-center gap-2">
              <input
                type="file"
                accept="image/*"
                onchange={uploadBackground}
                disabled={uploading}
                class="text-xs text-text-muted"
                aria-label={t("settings.invoicing.background_image")}
              />
              {#if background.file_id}
                <button
                  type="button"
                  class="text-xs text-text-muted underline"
                  onclick={() => setBackground({ file_id: null, use_logo: true })}
                  >{t("settings.invoicing.background_clear")}</button
                >
              {/if}
            </div>
            {#if uploadError}
              <p class="text-xs text-red-600 dark:text-red-400">{uploadError}</p>
            {/if}
          {/if}
          {#each [{ key: "opacity" as const, min: 0, max: 0.4, step: 0.005 }, { key: "scale" as const, min: 10, max: 160, step: 1 }, { key: "x" as const, min: 0, max: 100, step: 1 }, { key: "y" as const, min: 0, max: 100, step: 1 }, { key: "rotate" as const, min: -90, max: 90, step: 1 }] as slider (slider.key)}
            <div>
              <label
                for="bg-{slider.key}"
                class="mb-0.5 flex justify-between text-xs text-text-muted"
              >
                <span>{t(`settings.invoicing.background_${slider.key}`)}</span>
                <span class="tabular-nums">{background[slider.key]}</span>
              </label>
              <input
                id="bg-{slider.key}"
                type="range"
                min={slider.min}
                max={slider.max}
                step={slider.step}
                value={background[slider.key]}
                oninput={(e) => setBackground({ [slider.key]: Number(e.currentTarget.value) })}
                class="w-full accent-brand"
              />
            </div>
          {/each}
        {/if}
      </fieldset>
    {:else if tab === "layout"}
      <div class="flex items-start justify-between gap-3">
        <p class="text-xs text-text-muted">{t("settings.invoicing.layout_hint")}</p>
        <button
          type="button"
          class="shrink-0 text-xs text-text-muted underline"
          onclick={resetLayout}>{t("settings.invoicing.layout_reset")}</button
        >
      </div>
      <ul class="divide-y divide-border rounded-lg border border-border">
        {#each layout as block (block.key)}
          <li>
            <div class="flex items-center gap-2 px-2 py-1.5">
              <label class="flex min-w-0 flex-1 items-center gap-2 text-sm text-text">
                <input
                  type="checkbox"
                  checked={block.enabled}
                  disabled={block.locked}
                  onchange={() => toggleBlock(block.key)}
                  class="rounded border-border"
                />
                <span class="truncate {block.enabled ? '' : 'text-text-muted'}"
                  >{blockLabel(block.key)}</span
                >
                {#if block.locked}
                  <Lock
                    class="size-3 shrink-0 text-text-muted"
                    aria-label={t("settings.invoicing.layout_locked")}
                  />
                {/if}
              </label>
              {#if movable(block.key)}
                <button
                  type="button"
                  class={iconButton}
                  aria-label={t("settings.invoicing.layout_move_up")}
                  onclick={() => moveBlock(block.key, -1)}
                >
                  <ChevronUp class="size-4" />
                </button>
                <button
                  type="button"
                  class={iconButton}
                  aria-label={t("settings.invoicing.layout_move_down")}
                  onclick={() => moveBlock(block.key, 1)}
                >
                  <ChevronDown class="size-4" />
                </button>
              {/if}
              {#if block.fields.length}
                <button
                  type="button"
                  class="rounded px-2 py-0.5 text-xs text-text-muted hover:bg-surface hover:text-text"
                  aria-expanded={openBlock === block.key}
                  onclick={() => (openBlock = openBlock === block.key ? null : block.key)}
                  >{t("settings.invoicing.layout_fields")}</button
                >
              {/if}
            </div>
            {#if openBlock === block.key && block.fields.length}
              <ul class="border-t border-border bg-surface/50 py-1 pl-8 pr-2">
                {#each block.fields as field (field.key)}
                  <li class="py-0.5">
                    <div class="flex items-center gap-2">
                      <label class="flex min-w-0 flex-1 items-center gap-2 text-sm text-text">
                        <input
                          type="checkbox"
                          checked={field.enabled}
                          disabled={field.locked}
                          onchange={() => toggleField(block.key, field.key)}
                          class="rounded border-border"
                        />
                        <span class="truncate {field.enabled ? '' : 'text-text-muted'}"
                          >{fieldLabel(block.key, field.key)}</span
                        >
                        {#if field.locked}
                          <Lock
                            class="size-3 shrink-0 text-text-muted"
                            aria-label={t("settings.invoicing.layout_locked")}
                          />
                        {/if}
                      </label>
                      <button
                        type="button"
                        class={iconButton}
                        aria-label={t("settings.invoicing.layout_move_up")}
                        onclick={() => moveField(block.key, field.key, -1)}
                      >
                        <ChevronUp class="size-4" />
                      </button>
                      <button
                        type="button"
                        class={iconButton}
                        aria-label={t("settings.invoicing.layout_move_down")}
                        onclick={() => moveField(block.key, field.key, 1)}
                      >
                        <ChevronDown class="size-4" />
                      </button>
                    </div>
                    <!-- The tenant's own wording for the printed label. The placeholder is
                         what the document says today, so an empty box reads as "ours" rather
                         than as a field with no name. Only for a field that prints a label,
                         and only while it is switched on. -->
                    {#if field.labelled && field.enabled}
                      <div class="flex items-center gap-2 pb-1 pl-6 pr-[4.5rem] pt-0.5">
                        <input
                          type="text"
                          maxlength="60"
                          class="w-full rounded border border-border bg-surface px-2 py-1 text-sm text-text"
                          placeholder={fieldLabel(block.key, field.key)}
                          aria-label={t("settings.invoicing.field_label")}
                          value={field.label_i18n?.[labelLocale] ?? ""}
                          oninput={(event) =>
                            setFieldLabel(block.key, field.key, event.currentTarget.value)}
                        />
                      </div>
                    {/if}
                  </li>
                {/each}
              </ul>
            {/if}
          </li>
        {/each}
      </ul>
    {:else if tab === "texts"}
      <!-- One language at a time, chosen above: six stacked boxes made the tab read as a form
           that wanted all of them, when a document prints in exactly one language. -->
      {#each [{ block: "intro_i18n" as const, label: "intro_text" }, { block: "payment_i18n" as const, label: "payment_text" }, { block: "footer_i18n" as const, label: "footer_text" }] as entry (entry.block)}
        <div>
          <label
            for="tpl-{entry.block}-{labelLocale}"
            class="mb-1 block text-sm font-medium text-text"
          >
            {t(`settings.invoicing.${entry.label}`)}
          </label>
          <textarea
            id="tpl-{entry.block}-{labelLocale}"
            rows="2"
            value={config[entry.block]?.[labelLocale] ?? ""}
            oninput={(e) =>
              (config = {
                ...config,
                [entry.block]: { ...config[entry.block], [labelLocale]: e.currentTarget.value },
              })}
            class={inputClass}></textarea>
        </div>
      {/each}
    {:else if tab === "source"}
      <p class="text-xs text-text-muted">{t("settings.invoicing.source_hint")}</p>
      <div class="flex items-end gap-2">
        <div class="flex-1">
          <label for="tpl-source-from" class="mb-1 block text-sm font-medium text-text">
            {t("settings.invoicing.source_start_from")}
          </label>
          <select id="tpl-source-from" bind:value={sourceFrom} class={inputClass}>
            <option value="classic">{t("settings.invoicing.design.classic")}</option>
            <option value="letterhead">{t("settings.invoicing.design.letterhead")}</option>
          </select>
        </div>
        <button
          type="button"
          class="rounded-lg border border-border px-3 py-2 text-sm text-text hover:bg-surface"
          onclick={() => loadSource()}>{t("settings.invoicing.source_load")}</button
        >
      </div>
      <div>
        <label for="tpl-html" class="mb-1 block text-sm font-medium text-text">
          {t("settings.invoicing.source_html")}
        </label>
        <textarea
          id="tpl-html"
          rows="14"
          value={config.html ?? ""}
          oninput={(e) => (config = { ...config, html: e.currentTarget.value })}
          spellcheck="false"
          class={codeClass}></textarea>
      </div>
      <div>
        <label for="tpl-css" class="mb-1 block text-sm font-medium text-text">
          {t("settings.invoicing.source_css")}
        </label>
        <textarea
          id="tpl-css"
          rows="14"
          value={config.css ?? ""}
          oninput={(e) => (config = { ...config, css: e.currentTarget.value })}
          spellcheck="false"
          class={codeClass}></textarea>
      </div>
    {/if}
  </div>

  <div class="min-w-0">
    <div class="mb-2 flex items-center justify-between gap-2">
      <p class="text-sm font-medium text-text">{t("settings.invoicing.preview")}</p>
      {#if previewError}
        <p class="text-xs text-red-600 dark:text-red-400">{previewError}</p>
      {/if}
    </div>
    <div class="max-h-[70vh] overflow-y-auto rounded-lg border border-border bg-surface">
      <DocumentFrame
        srcdoc={previewHtml}
        loading={previewBusy && !previewHtml}
        title={t("settings.invoicing.preview")}
      />
    </div>
  </div>
</div>
