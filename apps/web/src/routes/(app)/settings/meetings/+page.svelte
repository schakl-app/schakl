<script lang="ts">
  /**
   * Instellingen → Vergaderingen: the consent statement before a recording, the house rules
   * the minutes are written to, and what the minutes document looks like.
   *
   * One form, one row. Every control is state the hidden inputs post, so a save carries the
   * whole row and never blanks a field that was not on the tab the admin had open (docs/UX.md:
   * saving must never blank the form — hence `busy.keep()`).
   */
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import Button from "$lib/core/ui/Button.svelte";
  import FormCheckbox from "$lib/core/ui/FormCheckbox.svelte";
  import MeetingDocumentEditor from "$lib/modules/meetings/MeetingDocumentEditor.svelte";

  let { data, form } = $props();
  const settings = $derived(data.settings);
  const busy = new InFlight();

  // Seeded once from the row; the editor owns them from here (the preview redraws from what is
  // being typed, never from the saved row).
  // svelte-ignore state_referenced_locally
  let design = $state(settings?.document_design ?? "standard");
  // svelte-ignore state_referenced_locally
  let customHtml = $state<string | null>(settings?.document_custom_html ?? null);
  // svelte-ignore state_referenced_locally
  let customCss = $state<string | null>(settings?.document_custom_css ?? null);
  // svelte-ignore state_referenced_locally
  let coverFileId = $state<string | null>(settings?.document_cover_file_id ?? null);
  // svelte-ignore state_referenced_locally
  let ticked = $state<string[]>([...(settings?.document_sections ?? [])]);
  // svelte-ignore state_referenced_locally
  let accent = $state(settings?.document_accent_color ?? "");
  // svelte-ignore state_referenced_locally
  let footer = $state(settings?.document_footer_text ?? "");
  // svelte-ignore state_referenced_locally
  let avatars = $state(settings?.document_avatars ?? true);

  const inputClass =
    "w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<svelte:head>
  <title>{pageTitle(t("settings.meetings.title"))}</title>
</svelte:head>

<h1 class="mt-2 mb-1 text-xl font-semibold text-text">{t("settings.meetings.title")}</h1>
<p class="mb-6 text-sm text-text-muted">{t("settings.meetings.subtitle")}</p>

<form method="POST" action="?/save" use:enhance={busy.keep()} class="max-w-7xl space-y-6">
  <section class="max-w-3xl rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="mb-3 text-base font-semibold text-text">{t("settings.meetings.consent_heading")}</h2>
    <label class="flex items-start gap-3 text-sm text-text">
      <FormCheckbox
        name="consent_required"
        checked={settings?.consent_required ?? true}
        class="mt-0.5 rounded border-border"
      />
      <span>
        <span class="block font-medium">{t("settings.meetings.consent_required")}</span>
        <span class="block text-xs text-text-muted"
          >{t("settings.meetings.consent_required_hint")}</span
        >
      </span>
    </label>
    <p class="mt-3 text-xs text-text-muted">
      {t("settings.meetings.retention_note", {
        days: String(settings?.audio_retention_days ?? 30),
      })}
    </p>
  </section>

  <section class="max-w-3xl rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="mb-1 text-base font-semibold text-text">{t("settings.meetings.ai_heading")}</h2>
    <p class="mb-3 text-xs text-text-muted">
      {t("settings.meetings.ai_link")}
      <a href="/settings/ai" class="text-brand underline-offset-2 hover:underline">
        {t("nav.settings")} → AI
      </a>
    </p>
    <label for="ai-instructions" class="mb-1 block text-sm font-medium text-text">
      {t("settings.meetings.ai_instructions")}
    </label>
    <textarea
      id="ai-instructions"
      name="ai_instructions"
      rows="5"
      class="{inputClass} resize-y"
      placeholder={t("settings.meetings.ai_instructions_placeholder")}
      value={settings?.ai_instructions ?? ""}></textarea>
    <p class="mt-1 text-xs text-text-muted">{t("settings.meetings.ai_instructions_hint")}</p>
  </section>

  <!-- Wider than the blocks above it: the editor draws the document beside its controls. -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="mb-1 text-base font-semibold text-text">
      {t("settings.meetings.document_heading")}
    </h2>
    <p class="mb-4 text-xs text-text-muted">{t("settings.meetings.document_hint")}</p>

    <div class="mb-4 grid gap-4 sm:grid-cols-2">
      <div>
        <label for="doc-accent" class="mb-1 block text-sm font-medium text-text">
          {t("settings.meetings.accent")}
        </label>
        <!-- Bound, not just posted: the preview redraws from what is being typed. -->
        <input
          id="doc-accent"
          name="document_accent_color"
          bind:value={accent}
          placeholder={t("settings.meetings.accent_placeholder")}
          class={inputClass}
        />
      </div>
      <div>
        <label for="doc-footer" class="mb-1 block text-sm font-medium text-text">
          {t("settings.meetings.footer_text")}
        </label>
        <input id="doc-footer" name="document_footer_text" bind:value={footer} class={inputClass} />
        <p class="mt-1 text-xs text-text-muted">{t("settings.meetings.footer_text_hint")}</p>
      </div>
    </div>
    <label class="mb-4 flex items-start gap-3 text-sm text-text">
      <FormCheckbox
        name="document_avatars"
        checked={settings?.document_avatars ?? true}
        class="mt-0.5 rounded border-border"
        onchange={(event) => (avatars = event.currentTarget.checked)}
      />
      <span>
        <span class="block font-medium">{t("settings.meetings.avatars")}</span>
        <span class="block text-xs text-text-muted">{t("settings.meetings.avatars_hint")}</span>
      </span>
    </label>

    <MeetingDocumentEditor
      sections={data.sections}
      bind:design
      bind:customHtml
      bind:customCss
      bind:coverFileId
      bind:tickedSections={ticked}
      accentColor={accent}
      footerText={footer}
      {avatars}
    />
  </section>

  {#if form?.saved}
    <p class="text-sm text-green-600 dark:text-green-400">{t("settings.meetings.saved")}</p>
  {:else if form?.error}
    <p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
  {/if}

  <Button type="submit" loading={busy.active}>{t("common.save")}</Button>
</form>
