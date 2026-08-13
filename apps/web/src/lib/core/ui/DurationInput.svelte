<script lang="ts">
  /**
   * Duration field (#326). A span of time is typed, not counted: "1:30", "100", "100m", "1h40"
   * and "1,5" all land on the same minutes, and the field shows the canonical "1:40" afterwards.
   *
   * It replaces `<input type="number" step="15">` in minutes, which was a browser rule stricter
   * than the API's — Chrome refused "100" with *"the two nearest valid values are 90 and 105"* on
   * a number the server accepts without complaint. So this control states **no** range of its
   * own: the API is the authority on what a duration may be, and a second copy of that rule here
   * is one that drifts. What it does refuse is text it cannot read — visibly, through the
   * browser's own validity machinery, so an unreadable value blocks the submit instead of quietly
   * posting a number nobody typed.
   *
   * What travels is the text, not a hidden number: the server runs `parsePostedMinutes` over it,
   * so a post with JS off and a hand-rolled request land on exactly the same value.
   */
  import { formatDurationInput, parseDurationText } from "$lib/core/duration";
  import { t } from "$lib/core/i18n";

  const HOUSE_INPUT =
    "w-full min-w-0 rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";

  let {
    name,
    minutes = $bindable(null),
    id = name,
    formId,
    required = false,
    placeholder,
    class: className = HOUSE_INPUT,
    ariaLabel,
    onchange,
  }: {
    /** Posted field name. Omit for a control that only drives client state (no form). */
    name?: string;
    minutes?: number | null;
    id?: string;
    /** Associate the posted value with an external <form id=…> (single-save layouts). */
    formId?: string;
    required?: boolean;
    placeholder?: string;
    class?: string;
    ariaLabel?: string;
    onchange?: (minutes: number | null) => void;
  } = $props();

  // Writable derived (`TimeInput`'s pattern): follows outside changes — a prefill, a picked task
  // — and holds the half-typed draft until it parses.
  let text = $derived(formatDurationInput(minutes ?? 0));
  let unreadable = $state(false);
  let field: HTMLInputElement | undefined = $state();

  /**
   * The refusal is the browser's own, so the submit stops here rather than at the API with the
   * typed text already thrown away. Written straight to the element rather than through an
   * `$effect`: clicking a submit button blurs the field, so `change` fires and the form submits
   * inside the same task — an effect flushed afterwards would arrive one submit too late.
   */
  function flag(bad: boolean) {
    unreadable = bad;
    field?.setCustomValidity(bad ? t("common.duration_invalid") : "");
  }

  function commit() {
    const raw = text.trim();
    if (!raw) {
      flag(false);
      set(null);
      return;
    }
    const parsed = parseDurationText(raw);
    if (parsed == null) {
      // Leave both the typed text and the stored minutes alone: guessing here is the one thing
      // this control must never do.
      flag(true);
      return;
    }
    flag(false);
    set(parsed > 0 ? Math.round(parsed) : null);
  }

  function set(value: number | null) {
    const changed = value !== minutes;
    minutes = value;
    text = formatDurationInput(value ?? 0); // canonicalise "100" → "1:40" even when nothing moved
    if (changed) onchange?.(value);
  }
</script>

<div class="min-w-0">
  <input
    bind:this={field}
    {id}
    {name}
    type="text"
    inputmode="text"
    autocomplete="off"
    form={formId}
    {required}
    bind:value={text}
    aria-label={ariaLabel}
    aria-invalid={unreadable ? "true" : undefined}
    placeholder={placeholder ?? t("common.duration_hint")}
    class="{className} {unreadable ? 'border-red-500 dark:border-red-500' : ''}"
    onchange={commit}
    oninput={() => flag(false)}
  />
  {#if unreadable}
    <p class="mt-1 text-xs text-red-600 dark:text-red-400">{t("common.duration_invalid")}</p>
  {/if}
</div>
