<script lang="ts">
  /**
   * The **one** language switcher for a surface that edits tenant translations (docs/UX.md).
   *
   * Render it once, at the top of the page, card or dialog whose fields carry tenant labels; every
   * `I18nTextField` (and every hand-rolled per-locale editor) below it follows the shared choice in
   * `core/i18n-edit`. Never one of these per field — that is the pattern it replaces.
   *
   * Two of them may be on screen at once (a page switcher plus one in the dialog it opens); they
   * read and write the same state, so they can't disagree, and the dialog stays self-sufficient
   * when it covers the page behind it.
   */
  import { localeLabel, t } from "$lib/core/i18n";
  import { editLocale, editLocales, resolveEditLocale } from "$lib/core/i18n-edit.svelte";

  let {
    locales = null,
    hint = true,
    class: klass = "",
  }: {
    /** Offer only these locales — for a surface whose languages come from data, not from the
     *  app's own catalog (the mail templates hold one row per `(kind, locale)`). */
    locales?: string[] | null;
    /** Show the "translations are optional" line under the control. */
    hint?: boolean;
    class?: string;
  } = $props();

  const options = $derived(locales ?? editLocales());
  const active = $derived(resolveEditLocale(options));
</script>

<div class={klass}>
  <div class="flex flex-wrap items-center justify-end gap-2">
    <span class="text-xs text-text-muted">{t("common.edit_language")}</span>
    <div
      class="flex gap-0.5 rounded-lg border border-border p-0.5"
      role="group"
      aria-label={t("common.edit_language")}
    >
      {#each options as locale (locale)}
        <button
          type="button"
          aria-pressed={active === locale}
          class="rounded-md px-2 py-0.5 text-xs font-medium {active === locale
            ? 'bg-brand text-white'
            : 'text-text-muted hover:bg-surface'}"
          onclick={() => editLocale.set(locale)}
        >
          {localeLabel(locale)}
        </button>
      {/each}
    </div>
  </div>
  {#if hint}
    <p class="mt-1 text-right text-xs text-text-muted">{t("common.translations_optional")}</p>
  {/if}
</div>
