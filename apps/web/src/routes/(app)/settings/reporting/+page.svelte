<script lang="ts">
  /**
   * Instellingen → Rapportage (issue #300).
   *
   * Three blocks, and the order is the argument: the **schedule** (what happens automatically),
   * the **tone of voice** (how the report is written), and the **templates** (what it looks
   * like). Auto-send lives in the first block and is off by default — the choice the owner
   * asked to be a setting rather than a policy, made where its consequences are visible.
   *
   * A client's own profile is not on this page. It belongs on the client.
   */
  import { ChevronDown, Plus, Trash2 } from "@lucide/svelte";
  import { SvelteSet } from "svelte/reactivity";

  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import Button from "$lib/core/ui/Button.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import FormCheckbox from "$lib/core/ui/FormCheckbox.svelte";
  import ReportTemplateEditor from "$lib/modules/reporting/ReportTemplateEditor.svelte";
  import type {
    ReportTemplate,
    ReportTone,
    SectionCatalogEntry,
  } from "$lib/modules/reporting/types";

  let { data, form } = $props();

  const busy = new InFlight();
  const schedule = $derived((data.settings?.schedule ?? {}) as Record<string, unknown>);

  let openTone = $state<string | null>(null);
  let openTemplate = $state<string | null>(null);
  let deleteToneId = $state("");
  let deleteTemplateId = $state("");

  const inputClass =
    "w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";

  /** A blank row for "add a tone of voice" — never persisted until Save. */
  const blankTone = {
    id: "",
    name: "",
    description: "",
    instructions: "",
    banned_phrases: [] as string[],
    preferred_phrases: [] as string[],
    is_default: false,
    active: true,
    position: 0,
  } as unknown as ReportTone;

  const blankTemplate = {
    id: "",
    name: "",
    audience: "client",
    design: "standard",
    layout: {},
    accent_color: null,
    intro_text: null,
    is_default: false,
  } as unknown as ReportTemplate;

  let addingTone = $state(false);
  let addingTemplate = $state(false);

  /**
   * The open template's editable state.
   *
   * One set of variables rather than one per card, because exactly one card is ever unfolded —
   * and the alternative, deriving each field from `template.*` inside the loop, would make the
   * live preview redraw from the *saved* row instead of from what is being typed.
   *
   * Seeded on open from the row itself, which is what carries `design`, `custom_html`,
   * `custom_css` and `cover_image_file_id` through a save that does not edit them (#300's
   * "absent is not clear"): the editor posts what it was given back, unchanged.
   */
  let editDesign = $state("standard");
  let editHtml = $state<string | null>(null);
  let editCss = $state<string | null>(null);
  let editCover = $state<string | null>(null);
  let editAccent = $state("");
  let editIntro = $state("");
  /** Reactive by identity, so the hidden `layout` field below recomputes as boxes are ticked. */
  const editOff = new SvelteSet<string>();

  function seed(template: ReportTemplate) {
    editDesign = template.design || "standard";
    editHtml = template.custom_html ?? null;
    editCss = template.custom_css ?? null;
    editCover = template.cover_image_file_id ?? null;
    editAccent = template.accent_color ?? "";
    editIntro = template.intro_text ?? "";
    editOff.clear();
    for (const key of disabledKeys(template)) editOff.add(key);
  }

  const clientSections = $derived(
    (data.sections as SectionCatalogEntry[]).filter((s) => s.audience !== "internal"),
  );
  const internalSections = $derived(
    (data.sections as SectionCatalogEntry[]).filter((s) => s.audience !== "client"),
  );

  /** The layout a template posts: every catalog key, in order, with its on/off state. */
  function layoutJson(audience: string, disabled: ReadonlySet<string>): string {
    const catalog = audience === "internal" ? internalSections : clientSections;
    return JSON.stringify(
      catalog.map((section) => ({ key: section.key, enabled: !disabled.has(section.key) })),
    );
  }

  function disabledKeys(template: ReportTemplate): string[] {
    const stored = (template.layout as { sections?: { key: string; enabled?: boolean }[] })
      ?.sections;
    return (stored ?? []).filter((s) => s.enabled === false).map((s) => s.key);
  }
</script>

<svelte:head>
  <title>{pageTitle(t("settings.reporting.title"))}</title>
</svelte:head>

<h1 class="mb-1 mt-2 text-xl font-semibold text-text">{t("settings.reporting.title")}</h1>
<p class="mb-6 text-sm text-text-muted">{t("settings.reporting.subtitle")}</p>

{#if form?.error}
  <p
    class="mb-4 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950 dark:text-red-300"
  >
    {t(form.error)}
  </p>
{/if}

<!-- ─── Schedule ─────────────────────────────────────────────────────────── -->
<section class="mb-8 max-w-3xl rounded-xl border border-border bg-surface-raised p-5">
  <h2 class="mb-1 text-base font-semibold text-text">{t("settings.reporting.schedule")}</h2>
  <p class="mb-4 text-sm text-text-muted">{t("settings.reporting.schedule_hint")}</p>

  <form method="POST" action="?/saveSettings" use:enhance={busy.keep("settings")} class="space-y-4">
    <div class="grid gap-4 sm:grid-cols-3">
      <div>
        <label for="cadence" class="mb-1 block text-sm font-medium text-text">
          {t("settings.reporting.cadence")}
        </label>
        <select
          id="cadence"
          name="cadence"
          class={inputClass}
          value={schedule.cadence ?? "monthly"}
        >
          <option value="monthly">{t("reporting.cadence.monthly")}</option>
          <option value="quarterly">{t("reporting.cadence.quarterly")}</option>
          <option value="off">{t("reporting.cadence.off")}</option>
        </select>
      </div>
      <div>
        <label for="day_of_month" class="mb-1 block text-sm font-medium text-text">
          {t("settings.reporting.day_of_month")}
        </label>
        <input
          id="day_of_month"
          name="day_of_month"
          type="number"
          min="1"
          max="28"
          class={inputClass}
          value={schedule.day_of_month ?? 5}
        />
        <p class="mt-1 text-xs text-text-muted">{t("settings.reporting.day_hint")}</p>
      </div>
      <div>
        <label for="hour" class="mb-1 block text-sm font-medium text-text">
          {t("settings.reporting.hour")}
        </label>
        <input
          id="hour"
          name="hour"
          type="number"
          min="0"
          max="23"
          class={inputClass}
          value={schedule.hour ?? 8}
        />
      </div>
    </div>

    <div class="grid gap-4 sm:grid-cols-2">
      <div>
        <label for="compare" class="mb-1 block text-sm font-medium text-text">
          {t("settings.reporting.compare")}
        </label>
        <select id="compare" name="compare" class={inputClass} value={schedule.compare ?? "year"}>
          <option value="year">{t("settings.reporting.compare_year")}</option>
          <option value="previous">{t("settings.reporting.compare_previous")}</option>
        </select>
      </div>
      <div>
        <label for="delivery" class="mb-1 block text-sm font-medium text-text">
          {t("settings.reporting.delivery")}
        </label>
        <select
          id="delivery"
          name="delivery"
          class={inputClass}
          value={schedule.delivery ?? "review"}
        >
          <option value="review">{t("reporting.delivery.review")}</option>
          <option value="auto">{t("reporting.delivery.auto")}</option>
        </select>
        <p class="mt-1 text-xs text-text-muted">{t("settings.reporting.delivery_hint")}</p>
      </div>
    </div>

    <label class="flex items-start gap-2 text-sm text-text">
      <FormCheckbox
        name="publish_to_portal"
        checked={schedule.publish_to_portal !== false}
        class="mt-0.5 rounded border-border"
      />
      <span>
        {t("settings.reporting.publish_to_portal")}
        <span class="mt-0.5 block text-xs text-text-muted">
          {t("settings.reporting.publish_to_portal_hint")}
        </span>
      </span>
    </label>

    <div>
      <label for="footer_text" class="mb-1 block text-sm font-medium text-text">
        {t("settings.reporting.footer_text")}
      </label>
      <textarea id="footer_text" name="footer_text" rows="2" class={inputClass}
        >{data.settings?.footer_text ?? ""}</textarea
      >
    </div>

    <input type="hidden" name="default_locale" value={data.settings?.default_locale ?? "nl"} />

    {#if form?.savedSettings}
      <p class="text-sm text-green-600 dark:text-green-400">{t("settings.reporting.saved")}</p>
    {/if}
    <Button type="submit" loading={busy.is("settings")} disabled={busy.active}>
      {t("common.save")}
    </Button>
  </form>
</section>

<!-- ─── Tone of voice ────────────────────────────────────────────────────── -->
<section class="mb-8 max-w-3xl">
  <div class="mb-3 flex items-center justify-between">
    <div>
      <h2 class="text-base font-semibold text-text">{t("settings.reporting.tones")}</h2>
      <p class="text-sm text-text-muted">{t("settings.reporting.tones_hint")}</p>
    </div>
    <Button variant="secondary" size="sm" onclick={() => (addingTone = !addingTone)}>
      <Plus size={14} />
      {t("settings.reporting.add_tone")}
    </Button>
  </div>

  <div class="space-y-3">
    {#each [...(addingTone ? [blankTone] : []), ...(data.tones as ReportTone[])] as tone (tone.id || "new")}
      {@const isOpen = openTone === (tone.id || "new") || (!tone.id && addingTone)}
      <div class="rounded-xl border border-border bg-surface-raised">
        <button
          type="button"
          class="flex w-full items-center gap-3 px-5 py-4 text-left"
          onclick={() => (openTone = isOpen ? null : tone.id || "new")}
        >
          <span class="font-medium text-text">
            {tone.name || t("settings.reporting.new_tone")}
          </span>
          {#if tone.is_default}
            <span class="rounded-full bg-surface px-2 py-0.5 text-xs text-text-muted">
              {t("settings.reporting.default")}
            </span>
          {/if}
          <ChevronDown
            size={16}
            class="ml-auto text-text-muted transition-transform {isOpen ? 'rotate-180' : ''}"
          />
        </button>

        {#if isOpen}
          <form
            method="POST"
            action="?/saveTone"
            use:enhance={busy.keep(tone.id || "new-tone")}
            class="space-y-4 border-t border-border px-5 py-4"
          >
            <input type="hidden" name="id" value={tone.id} />
            <div class="grid gap-4 sm:grid-cols-2">
              <div>
                <label
                  for={`tone-name-${tone.id}`}
                  class="mb-1 block text-sm font-medium text-text"
                >
                  {t("settings.reporting.tone_name")}
                </label>
                <input
                  id={`tone-name-${tone.id}`}
                  name="name"
                  class={inputClass}
                  value={tone.name}
                />
              </div>
              <div>
                <label
                  for={`tone-desc-${tone.id}`}
                  class="mb-1 block text-sm font-medium text-text"
                >
                  {t("settings.reporting.tone_description")}
                </label>
                <input
                  id={`tone-desc-${tone.id}`}
                  name="description"
                  class={inputClass}
                  value={tone.description ?? ""}
                />
              </div>
            </div>

            <div>
              <label
                for={`tone-instructions-${tone.id}`}
                class="mb-1 block text-sm font-medium text-text"
              >
                {t("settings.reporting.instructions")}
              </label>
              <textarea
                id={`tone-instructions-${tone.id}`}
                name="instructions"
                rows="12"
                class="{inputClass} font-mono text-xs leading-relaxed">{tone.instructions}</textarea
              >
              <p class="mt-1 text-xs text-text-muted">
                {t("settings.reporting.instructions_hint")}
              </p>
            </div>

            <div class="grid gap-4 sm:grid-cols-2">
              <div>
                <label
                  for={`tone-banned-${tone.id}`}
                  class="mb-1 block text-sm font-medium text-text"
                >
                  {t("settings.reporting.banned")}
                </label>
                <textarea
                  id={`tone-banned-${tone.id}`}
                  name="banned_phrases"
                  rows="6"
                  class={inputClass}>{(tone.banned_phrases ?? []).join("\n")}</textarea
                >
                <p class="mt-1 text-xs text-text-muted">{t("settings.reporting.banned_hint")}</p>
              </div>
              <div>
                <label
                  for={`tone-preferred-${tone.id}`}
                  class="mb-1 block text-sm font-medium text-text"
                >
                  {t("settings.reporting.preferred")}
                </label>
                <textarea
                  id={`tone-preferred-${tone.id}`}
                  name="preferred_phrases"
                  rows="6"
                  class={inputClass}>{(tone.preferred_phrases ?? []).join("\n")}</textarea
                >
              </div>
            </div>

            <label class="flex items-center gap-2 text-sm text-text">
              <FormCheckbox
                name="is_default"
                checked={tone.is_default}
                class="rounded border-border"
              />
              <span>{t("settings.reporting.is_default")}</span>
            </label>

            <div class="flex items-center gap-2">
              <Button type="submit" loading={busy.is(tone.id || "new-tone")} disabled={busy.active}>
                {t("common.save")}
              </Button>
              {#if tone.id}
                <button
                  type="button"
                  class="ml-auto rounded-lg p-2 text-text-muted hover:bg-surface hover:text-red-600"
                  aria-label={t("common.delete")}
                  onclick={() => (deleteToneId = tone.id)}
                >
                  <Trash2 size={16} />
                </button>
              {/if}
            </div>
          </form>
        {/if}
      </div>
    {/each}
  </div>
</section>

<!-- ─── Templates ────────────────────────────────────────────────────────── -->
<!-- Wider than the blocks above it: the editor draws the document beside its controls, and a
     preview squeezed into a third of a 3xl column is a thumbnail, not a proof. -->
<section class="mb-8 max-w-7xl">
  <div class="mb-3 flex items-center justify-between">
    <div>
      <h2 class="text-base font-semibold text-text">{t("settings.reporting.templates")}</h2>
      <p class="text-sm text-text-muted">{t("settings.reporting.templates_hint")}</p>
    </div>
    <Button
      variant="secondary"
      size="sm"
      onclick={() => {
        addingTemplate = !addingTemplate;
        if (addingTemplate) seed(blankTemplate);
      }}
    >
      <Plus size={14} />
      {t("settings.reporting.add_template")}
    </Button>
  </div>

  <div class="space-y-3">
    {#each [...(addingTemplate ? [blankTemplate] : []), ...(data.templates as ReportTemplate[])] as template (template.id || "new")}
      {@const isOpen = openTemplate === (template.id || "new") || (!template.id && addingTemplate)}
      <div class="rounded-xl border border-border bg-surface-raised">
        <button
          type="button"
          class="flex w-full items-center gap-3 px-5 py-4 text-left"
          onclick={() => {
            if (isOpen) {
              openTemplate = null;
              return;
            }
            seed(template);
            openTemplate = template.id || "new";
          }}
        >
          <span class="font-medium text-text">
            {template.name || t("settings.reporting.new_template")}
          </span>
          <span class="rounded-full bg-surface px-2 py-0.5 text-xs text-text-muted">
            {t(`reporting.audience.${template.audience}`)}
          </span>
          {#if template.is_default}
            <span class="rounded-full bg-surface px-2 py-0.5 text-xs text-text-muted">
              {t("settings.reporting.default")}
            </span>
          {/if}
          <ChevronDown
            size={16}
            class="ml-auto text-text-muted transition-transform {isOpen ? 'rotate-180' : ''}"
          />
        </button>

        {#if isOpen}
          <form
            method="POST"
            action="?/saveTemplate"
            use:enhance={busy.keep(template.id || "new-template")}
            class="space-y-4 border-t border-border px-5 py-4"
          >
            <input type="hidden" name="id" value={template.id} />
            <input type="hidden" name="layout" value={layoutJson(template.audience, editOff)} />
            <div class="grid gap-4 sm:grid-cols-3">
              <div>
                <label
                  for={`tpl-name-${template.id}`}
                  class="mb-1 block text-sm font-medium text-text"
                >
                  {t("settings.reporting.template_name")}
                </label>
                <input
                  id={`tpl-name-${template.id}`}
                  name="name"
                  class={inputClass}
                  value={template.name}
                />
              </div>
              <div>
                <label
                  for={`tpl-audience-${template.id}`}
                  class="mb-1 block text-sm font-medium text-text"
                >
                  {t("settings.reporting.audience")}
                </label>
                <select
                  id={`tpl-audience-${template.id}`}
                  name="audience"
                  class={inputClass}
                  value={template.audience}
                >
                  <option value="client">{t("reporting.audience.client")}</option>
                  <option value="internal">{t("reporting.audience.internal")}</option>
                </select>
              </div>
              <div>
                <label
                  for={`tpl-accent-${template.id}`}
                  class="mb-1 block text-sm font-medium text-text"
                >
                  {t("settings.reporting.accent")}
                </label>
                <!-- Bound, not just posted: the preview beside the editor has to redraw from
                     what is being typed, and a plain `value=` would leave it on the last
                     saved colour until the form came back. -->
                <input
                  id={`tpl-accent-${template.id}`}
                  name="accent_color"
                  class={inputClass}
                  placeholder={t("settings.reporting.accent_placeholder")}
                  bind:value={editAccent}
                />
              </div>
            </div>

            <div>
              <label
                for={`tpl-intro-${template.id}`}
                class="mb-1 block text-sm font-medium text-text"
              >
                {t("settings.reporting.intro_text")}
              </label>
              <textarea
                id={`tpl-intro-${template.id}`}
                name="intro_text"
                rows="4"
                class={inputClass}
                bind:value={editIntro}></textarea>
              <p class="mt-1 text-xs text-text-muted">{t("settings.reporting.intro_hint")}</p>
            </div>

            <ReportTemplateEditor
              {template}
              sections={template.audience === "internal" ? internalSections : clientSections}
              audience={template.audience}
              bind:design={editDesign}
              bind:customHtml={editHtml}
              bind:customCss={editCss}
              bind:coverFileId={editCover}
              disabledSections={editOff}
              accentColor={editAccent}
              introText={editIntro}
            />

            <label class="flex items-center gap-2 text-sm text-text">
              <FormCheckbox
                name="is_default"
                checked={template.is_default}
                class="rounded border-border"
              />
              <span>{t("settings.reporting.template_is_default")}</span>
            </label>

            <div class="flex items-center gap-2">
              <Button
                type="submit"
                loading={busy.is(template.id || "new-template")}
                disabled={busy.active}
              >
                {t("common.save")}
              </Button>
              {#if template.id}
                <button
                  type="button"
                  class="ml-auto rounded-lg p-2 text-text-muted hover:bg-surface hover:text-red-600"
                  aria-label={t("common.delete")}
                  onclick={() => (deleteTemplateId = template.id)}
                >
                  <Trash2 size={16} />
                </button>
              {/if}
            </div>
          </form>
        {/if}
      </div>
    {/each}
  </div>
</section>

<ConfirmDialog
  open={Boolean(deleteToneId)}
  title={t("settings.reporting.delete_tone")}
  message={t("settings.reporting.delete_tone_confirm")}
  action="?/deleteTone"
  fields={{ id: deleteToneId }}
/>
<ConfirmDialog
  open={Boolean(deleteTemplateId)}
  title={t("settings.reporting.delete_template")}
  message={t("settings.reporting.delete_template_confirm")}
  action="?/deleteTemplate"
  fields={{ id: deleteTemplateId }}
/>
