<script lang="ts">
  /**
   * How far the billing cron takes a recurring invoice by itself.
   *
   * A radio card list rather than a `<select>`, because these are four escalating levels and
   * each needs a sentence: the difference between them is how much of a mistake reaches the
   * client, and that is not something a four-word option label can carry. It is the
   * employment wizard's `radioRow` shape (docs/UX.md), used for the same reason.
   *
   * With `inheritable`, a fifth row leads: *follow the organisation setting*. That is the
   * default for an agreement and posts an empty value, which the API stores as NULL — a third
   * state meaning **inherit**, never "off". Naming the inherited level in the hint is the
   * point of the row: an admin choosing "follow" should not have to open another screen to
   * find out what they just chose.
   */
  import { t } from "$lib/core/i18n";

  import { AUTO_INVOICE_MODES } from "./types";
  import type { AutoInvoiceMode } from "./types";

  let {
    name,
    value = "",
    /** Offer "follow the organisation setting" (an agreement) or not (the org itself). */
    inheritable = false,
    /** The org's level, named in the inherit row's hint so the choice is legible. Omit it
     *  where the caller cannot know it: a hint naming the wrong level is worse than none. */
    orgMode = null,
    disabled = false,
    formId,
  }: {
    name: string;
    value?: AutoInvoiceMode | "" | null;
    inheritable?: boolean;
    orgMode?: AutoInvoiceMode | null;
    disabled?: boolean;
    formId?: string;
  } = $props();

  // Bound, never a one-way `checked` (docs/UX.md): a radio rendered one-way loses its mark on
  // hydration, and the next save then silently strips what the user never touched.
  // svelte-ignore state_referenced_locally
  let chosen = $state<string>(value ?? "");

  const rowClass =
    "flex cursor-pointer items-start gap-2 rounded-lg border border-border p-3 text-sm";
</script>

<fieldset class="space-y-2" {disabled}>
  <legend class="mb-1 text-xs font-semibold uppercase tracking-wide text-text-muted">
    {t("invoicing.auto.mode")}
  </legend>

  {#if inheritable}
    <label class={rowClass}>
      <input type="radio" class="mt-0.5" value="" bind:group={chosen} {name} form={formId} />
      <span>
        <span class="font-medium text-text">{t("invoicing.auto.inherit")}</span>
        {#if orgMode}
          <span class="mt-0.5 block text-xs text-text-muted">
            {t("invoicing.auto.inherit_hint", { mode: t(`invoicing.auto.${orgMode}`) })}
          </span>
        {/if}
      </span>
    </label>
  {/if}

  {#each AUTO_INVOICE_MODES as mode (mode)}
    <label class={rowClass}>
      <input type="radio" class="mt-0.5" value={mode} bind:group={chosen} {name} form={formId} />
      <span>
        <span class="font-medium text-text">{t(`invoicing.auto.${mode}`)}</span>
        <span class="mt-0.5 block text-xs text-text-muted">
          {t(`invoicing.auto.${mode}_hint`)}
        </span>
      </span>
    </label>
  {/each}
</fieldset>
