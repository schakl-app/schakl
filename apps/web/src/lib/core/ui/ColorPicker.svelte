<script lang="ts">
  /**
   * A lean, dependency-free colour picker for the calendar's personal feed colours (#281): the
   * shared preset tokens as swatches, a native `<input type="color">` for any custom hue, a
   * `#hex` field, and a "default" reset. `value` is a token, a `#hex`, or `""` (no override —
   * inherit the feed / leave-type colour). Renders inline; the caller owns open/close and
   * persists on `onchange`. Deliberately not a library (CLAUDE.md §11, lean code) — the native
   * control is already a good picker, and the tokens keep parity with the rest of the app.
   */
  import { t } from "$lib/core/i18n";
  import { isHexColor, labelDotClass, LABEL_COLORS } from "$lib/core/ui/colors";

  let {
    value = $bindable(""),
    onchange,
  }: {
    value?: string;
    /** Fired on every pick/clear so the caller can persist immediately (like the feed toggles). */
    onchange?: (value: string) => void;
  } = $props();

  // The native colour input needs a concrete 6-digit hex; seed it from the current hue or a grey.
  // `hexDraft` previews the drag locally; `pick` (which persists) fires only on a committed
  // `change` / preset click, so hauling the native slider around doesn't spam a reload per frame.
  let hexDraft = $state(isHexColor(value) ? value : "#4f46e5");

  function pick(next: string) {
    value = next;
    if (isHexColor(next)) hexDraft = next;
    onchange?.(next);
  }

  function commitHex(next: string) {
    const trimmed = next.trim();
    if (isHexColor(trimmed)) pick(trimmed);
  }
</script>

<div class="space-y-2 p-2">
  <div class="flex flex-wrap gap-1.5">
    {#each LABEL_COLORS as token (token)}
      <button
        type="button"
        aria-label={token}
        class="h-5 w-5 rounded-full {labelDotClass(token)} {value === token
          ? 'ring-2 ring-text ring-offset-1'
          : ''}"
        onclick={() => pick(token)}
      ></button>
    {/each}
  </div>
  <div class="flex items-center gap-2">
    <input
      type="color"
      aria-label={t("calendar.color.custom")}
      value={hexDraft}
      oninput={(e) => (hexDraft = e.currentTarget.value)}
      onchange={(e) => commitHex(e.currentTarget.value)}
      class="h-7 w-9 shrink-0 cursor-pointer rounded border border-border bg-transparent p-0.5"
    />
    <input
      type="text"
      spellcheck="false"
      autocomplete="off"
      placeholder="#7c3aed"
      aria-label={t("calendar.color.hex")}
      value={isHexColor(value) ? value : ""}
      onchange={(e) => commitHex(e.currentTarget.value)}
      class="w-24 rounded border border-border bg-surface px-2 py-1 text-xs text-text"
    />
    <button
      type="button"
      class="ml-auto text-xs hover:text-text {value === ''
        ? 'font-semibold text-text'
        : 'text-text-muted'}"
      onclick={() => pick("")}
    >
      {t("calendar.color.reset")}
    </button>
  </div>
</div>
