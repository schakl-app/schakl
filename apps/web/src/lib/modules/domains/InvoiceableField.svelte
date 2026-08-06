<script lang="ts">
  /**
   * Whether this domain is invoiced at all (#298).
   *
   * Three radio rows rather than a checkbox, because the answer has three states and the
   * middle one is the default: *follow the register*. A checkbox would have to pick a side
   * for every domain nobody has thought about yet, and the register is the only thing that
   * knows which side that is — an agency's list mixes names it renews for the client with
   * names the client registered themselves and merely asked us to point somewhere.
   *
   * The follow row names what the register currently answers, the `AutoInvoiceModeField`
   * discipline: an admin choosing "follow" should not have to open another screen to find out
   * what they just chose. Where the caller cannot know (a create form — the record does not
   * exist yet, so no register has been asked), the hint is omitted rather than guessed.
   */
  import { t } from "$lib/core/i18n";

  let {
    name,
    value = null,
    /** Which rule answers today: `explicit` | `register` | `default`. Null on a create form. */
    source = null,
    /** The registers that hold this registration, by key (`oxxa`, `cloudflare`). */
    registers = [],
    disabled = false,
    formId,
    onchoose,
  }: {
    name: string;
    value?: boolean | null;
    source?: "explicit" | "register" | "default" | null;
    registers?: string[];
    disabled?: boolean;
    formId?: string;
    /** The current row, for a caller that states it elsewhere — a collapsed section's summary
     *  (`DomainForm`). Read-only: the radios stay this component's own state. */
    onchoose?: (chosen: string) => void;
  } = $props();

  // Bound, never one-way `checked` (docs/UX.md): a radio rendered one-way loses its mark on
  // hydration, and the next save then silently strips what the user never touched.
  // svelte-ignore state_referenced_locally
  let chosen = $state<string>(value === true ? "yes" : value === false ? "no" : "");
  $effect(() => onchoose?.(chosen));

  /** What the register says right now, in one sentence — or nothing, on a create form. */
  const followHint = $derived.by(() => {
    if (source === null) return "";
    if (registers.length > 0) {
      return t("domains.invoiceable.follow_hint_held", {
        register: registers.map((key) => t(`domains.register.${key}`)).join(", "),
      });
    }
    // `explicit` means the flag is set, so the register is not being consulted — but the row
    // still has to say what choosing it *would* do, which is the same question either way.
    return source === "default"
      ? t("domains.invoiceable.follow_hint_none")
      : t("domains.invoiceable.follow_hint_absent");
  });

  const rowClass =
    "flex cursor-pointer items-start gap-2 rounded-lg border border-border p-3 text-sm";
</script>

<fieldset class="space-y-2" {disabled}>
  <legend class="mb-1 text-xs font-semibold uppercase tracking-wide text-text-muted">
    {t("domains.invoiceable.legend")}
  </legend>

  <label class={rowClass}>
    <input type="radio" class="mt-0.5" value="" bind:group={chosen} {name} form={formId} />
    <span>
      <span class="font-medium text-text">{t("domains.invoiceable.follow")}</span>
      {#if followHint}
        <span class="mt-0.5 block text-xs text-text-muted">{followHint}</span>
      {/if}
    </span>
  </label>

  <label class={rowClass}>
    <input type="radio" class="mt-0.5" value="yes" bind:group={chosen} {name} form={formId} />
    <span>
      <span class="font-medium text-text">{t("domains.invoiceable.yes")}</span>
      <span class="mt-0.5 block text-xs text-text-muted">{t("domains.invoiceable.yes_hint")}</span>
    </span>
  </label>

  <label class={rowClass}>
    <input type="radio" class="mt-0.5" value="no" bind:group={chosen} {name} form={formId} />
    <span>
      <span class="font-medium text-text">{t("domains.invoiceable.no")}</span>
      <span class="mt-0.5 block text-xs text-text-muted">{t("domains.invoiceable.no_hint")}</span>
    </span>
  </label>
</fieldset>
