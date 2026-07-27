<script lang="ts">
  /**
   * A number-format input that shows what it produces, live.
   *
   * "Does `K{jaar}-{seq:4}` work?" is not answerable from a static hint listing the tokens —
   * which is what invoicing shipped with, and why a typo only surfaced as a 422 on save. This
   * renders the next two numbers as you type and names the problem when the template is
   * unusable, so the answer arrives before the save.
   */
  import { t } from "$lib/core/i18n";
  import { formatNumber, formatValid, NUMBER_TOKENS } from "$lib/core/numbering";

  let {
    id,
    name,
    label,
    value = $bindable(""),
    nextSeq = 1,
    class: inputClass = "",
  }: {
    id: string;
    name: string;
    label: string;
    value?: string;
    /** The counter the next allocation will use, so the preview shows real upcoming numbers. */
    nextSeq?: number;
    class?: string;
  } = $props();

  // What a form reset falls back to: the value this field was born with (i.e. what the server
  // has). Without it `reset()` restores the empty `value` attribute a Svelte-bound input never
  // sets, and Svelte writes that emptiness back into `value` — so saving blanks the field.
  // Belt-and-braces with `InFlight.keep()`: this makes the field safe in *any* form.
  const savedValue = value;

  const year = new Date().getFullYear();
  const valid = $derived(formatValid(value));
  const preview = $derived(
    valid ? [nextSeq, nextSeq + 1].map((seq) => formatNumber(value, year, seq)) : [],
  );
</script>

<div>
  <label for={id} class="mb-1 block text-sm font-medium text-text">{label}</label>
  <input
    {id}
    {name}
    bind:value
    defaultValue={savedValue}
    class={inputClass}
    spellcheck="false"
    autocomplete="off"
  />
  {#if value.trim() && !valid}
    <p class="mt-1 text-xs text-red-600 dark:text-red-400">
      {t("errors.companies.invalid_number_format")}
    </p>
  {:else if preview.length > 0}
    <p class="mt-1 text-xs text-text-muted">
      {t("numbering.preview")}
      <span class="font-mono text-text">{preview.join(", ")}</span>
    </p>
  {/if}
  <p class="mt-1 text-xs text-text-muted">
    {t("numbering.tokens")}
    <span class="font-mono">{NUMBER_TOKENS.join(" · ")}</span>
  </p>
</div>
