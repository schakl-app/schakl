<script lang="ts">
  /**
   * One tenant-translated text field. The editor shows a **single** input — never two side-by-side
   * — and which language it shows comes from the surface's own `I18nLocaleSwitcher`
   * (`core/i18n-edit`), not from a switcher on this field: eight translatable labels on one screen
   * used to draw eight switchers to flip one at a time (owner feedback, 2026-08-05).
   *
   * **Every translation is optional** — a missing language falls back at render time, never blocks
   * a save. Each locale still posts its own input (`<basename>_<locale>`), the inactive ones hidden
   * but present, so existing form actions keep reading `label_nl` / `label_en` unchanged.
   * Deliberately no `required`: a required attribute on a hidden input blocks the submit invisibly,
   * and the policy is that one language is enough.
   */
  import { editLocales, resolveEditLocale } from "$lib/core/i18n-edit.svelte";

  let {
    label,
    basename,
    values = {},
    locales = editLocales(),
    idPrefix = basename,
    textarea = false,
    rows = 3,
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
    /** Placeholder shown when a locale is blank — e.g. the value it would fall back to. */
    placeholder?: string;
  } = $props();

  const active = $derived(resolveEditLocale(locales));
  // Deliberate initial capture: the record's stored labels seed the editor once.
  // svelte-ignore state_referenced_locally
  let texts = $state<Record<string, string>>(
    Object.fromEntries(locales.map((locale) => [locale, values[locale] ?? ""])),
  );

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand";
</script>

<div>
  <label for={`${idPrefix}-${active}`} class="mb-1 block text-sm text-text">{label}</label>
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
</div>
