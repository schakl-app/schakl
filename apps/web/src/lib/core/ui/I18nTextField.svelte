<script lang="ts">
  /**
   * One tenant-translated text field with a language switcher (owner feedback): instead of
   * two side-by-side inputs the editor shows a single field and NL/EN tabs, and **every
   * translation is optional** — a missing language falls back at render time, never blocks
   * a save. Each locale still posts its own input (`<basename>_<locale>`), the inactive
   * ones hidden but present, so existing form actions keep reading `label_nl` / `label_en`
   * unchanged. Deliberately no `required`: a required attribute on a hidden input blocks
   * the submit invisibly, and the policy is that one language is enough.
   */
  import { t } from "$lib/core/i18n";

  let {
    label,
    basename,
    values = {},
    locales = ["nl", "en"],
    idPrefix = basename,
    textarea = false,
    rows = 3,
    hint = true,
    placeholder = "",
  }: {
    /** The field's visible label (e.g. "Label", "Naam"). */
    label: string;
    /** Input name prefix: `label` posts `label_nl` + `label_en`. */
    basename: string;
    /** Initial per-locale values (`label_i18n` / `name_i18n` of the record). */
    values?: Record<string, string | null | undefined>;
    locales?: string[];
    idPrefix?: string;
    textarea?: boolean;
    rows?: number;
    /** Show the one-line "translations are optional" hint under the field. */
    hint?: boolean;
    /** Placeholder shown when a locale is blank — e.g. the value it would fall back to. */
    placeholder?: string;
  } = $props();

  let active = $state(locales[0]);
  // Deliberate initial capture: the record's stored labels seed the editor once.
  // svelte-ignore state_referenced_locally
  let texts = $state<Record<string, string>>(
    Object.fromEntries(locales.map((locale) => [locale, values[locale] ?? ""])),
  );

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand";
</script>

<div>
  <div class="mb-1 flex items-center justify-between gap-2">
    <label for={`${idPrefix}-${active}`} class="block text-sm text-text">{label}</label>
    <div class="flex gap-0.5" role="tablist">
      {#each locales as locale (locale)}
        <button
          type="button"
          role="tab"
          aria-selected={active === locale}
          class="rounded px-1.5 py-0.5 text-[11px] font-medium uppercase {active === locale
            ? 'bg-brand text-white'
            : texts[locale]?.trim()
              ? 'text-text-muted hover:bg-surface'
              : 'text-text-muted/50 hover:bg-surface'}"
          onclick={() => (active = locale)}
        >
          {locale}
        </button>
      {/each}
    </div>
  </div>
  {#each locales as locale (locale)}
    <div class={active === locale ? "" : "hidden"}>
      {#if textarea}
        <textarea
          id={`${idPrefix}-${locale}`}
          name={`${basename}_${locale}`}
          {rows}
          {placeholder}
          bind:value={texts[locale]}
          class={inputClass}></textarea>
      {:else}
        <input
          id={`${idPrefix}-${locale}`}
          name={`${basename}_${locale}`}
          {placeholder}
          bind:value={texts[locale]}
          class={inputClass}
        />
      {/if}
    </div>
  {/each}
  {#if hint}
    <p class="mt-1 text-xs text-text-muted">{t("common.translations_optional")}</p>
  {/if}
</div>
