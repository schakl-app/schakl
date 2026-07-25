<script lang="ts">
  /**
   * The TLD price-increase dialog (#250) — the #231 preview-then-apply shape applied to the
   * TLD list. Scope is one Combobox: every priced TLD or a single one; a row's ⋮ shortcut
   * opens it `locked` to that TLD, so the field shows read-only. The preview is the API's
   * own computation: the numbers shown are exactly what an apply writes.
   */
  import { enhance } from "$app/forms";
  import type { SubmitFunction } from "@sveltejs/kit";
  import { fmtMoney } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";

  interface ScopeItem {
    value: string;
    label: string;
  }
  interface PricePreview {
    items: { tld: string; current_amount: string; new_amount: string; domain_count: number }[];
  }

  let {
    open = $bindable(false),
    scope = $bindable("all"),
    scopeItems,
    locked = false,
    form,
  }: {
    open?: boolean;
    /** `all` or `tld:<tld>`. */
    scope?: string;
    scopeItems: ScopeItem[];
    /** From a row's ⋮ shortcut: the scope is that row's, shown read-only. */
    locked?: boolean;
    form: {
      pricePreview?: PricePreview | null;
      priceScope?: string | null;
      priceError?: string | null;
      priceApplied?: number | null;
    } | null;
  } = $props();

  let priceMode = $state<"percent" | "amount" | "set">("percent");
  const PRICE_MODES = ["percent", "amount", "set"] as const;

  const busy = new InFlight();
  // Preview and apply share the form: key off the clicked button's formaction.
  const submit: SubmitFunction = (input) =>
    busy.wrap(
      input.submitter?.getAttribute("formaction") === "?/applyPriceIncrease" ? "apply" : "preview",
      () =>
        ({ result, update }) => {
          if (result.type === "success" && result.data && "priceApplied" in result.data) {
            open = false;
          }
          void update({ reset: false });
        },
    )(input);

  const scopeLabel = $derived(scopeItems.find((i) => i.value === scope)?.label ?? "");
  // A preview made for another scope (an earlier open) would mislead — render only a match.
  const preview = $derived((form?.priceScope ?? "") === scope ? form?.pricePreview : null);

  const money = (value: string | number | null | undefined) =>
    value == null ? "—" : fmtMoney(Number(value));

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<Modal bind:open title={t("domains.price_increase.title")}>
  <!-- Enter previews (the safe default); only the explicit Doorvoeren button applies. -->
  <form method="POST" action="?/previewPriceIncrease" use:enhance={submit} class="space-y-4">
    <p class="text-sm text-text-muted">{t("domains.price_increase.help")}</p>
    <div class="grid gap-3 sm:grid-cols-2">
      <div>
        <label for="tpi-mode" class="mb-1 block text-sm font-medium text-text"
          >{t("domains.price_increase.mode")}</label
        >
        <select id="tpi-mode" name="mode" bind:value={priceMode} class={inputClass}>
          {#each PRICE_MODES as mode (mode)}
            <option value={mode}>{t(`domains.price_increase.mode_${mode}`)}</option>
          {/each}
        </select>
      </div>
      <div>
        <label for="tpi-value" class="mb-1 block text-sm font-medium text-text"
          >{priceMode === "percent"
            ? t("domains.price_increase.value_percent")
            : t("domains.price_increase.value_amount")}</label
        >
        <input id="tpi-value" name="value" type="number" step="0.01" required class={inputClass} />
      </div>
      <div>
        <label for="tpi-from" class="mb-1 block text-sm font-medium text-text"
          >{t("domains.price_increase.valid_from")}</label
        >
        <DateInput name="valid_from" id="tpi-from" required value="" />
      </div>
      <div>
        <label for="tpi-scope" class="mb-1 block text-sm font-medium text-text"
          >{t("domains.price_increase.scope")}</label
        >
        {#if locked}
          <input
            id="tpi-scope"
            value={scopeLabel}
            disabled
            class="{inputClass} bg-surface text-text-muted"
          />
          <input type="hidden" name="scope" value={scope} />
        {:else}
          <Combobox
            items={scopeItems}
            name="scope"
            bind:value={scope}
            allowEmpty={false}
            id="tpi-scope"
            placeholder={t("domains.price_increase.scope")}
          />
        {/if}
      </div>
    </div>

    {#if preview}
      <div class="max-h-64 overflow-y-auto rounded-lg border border-border">
        {#if preview.items.length === 0}
          <p class="p-4 text-sm text-text-muted">{t("domains.price_increase.empty")}</p>
        {:else}
          <table class="w-full text-sm">
            <thead>
              <tr class="border-b border-border text-left text-xs text-text-muted">
                <th class="px-3 py-2 font-medium">{t("domains.tld_prices.tld")}</th>
                <th class="px-3 py-2 text-right font-medium"
                  >{t("domains.price_increase.domains")}</th
                >
                <th class="px-3 py-2 text-right font-medium"
                  >{t("domains.price_increase.current")}</th
                >
                <th class="px-3 py-2 text-right font-medium">{t("domains.price_increase.new")}</th>
              </tr>
            </thead>
            <tbody>
              {#each preview.items as item (item.tld)}
                <tr class="border-b border-border last:border-b-0">
                  <td class="px-3 py-1.5 font-medium text-text">.{item.tld}</td>
                  <td class="px-3 py-1.5 text-right tabular-nums text-text-muted"
                    >{item.domain_count}</td
                  >
                  <td class="px-3 py-1.5 text-right tabular-nums text-text-muted"
                    >{money(item.current_amount)}</td
                  >
                  <td class="px-3 py-1.5 text-right font-medium tabular-nums text-text"
                    >{money(item.new_amount)}</td
                  >
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>
    {/if}

    {#if form?.priceError}
      <p class="text-sm text-red-600 dark:text-red-400">{t(form.priceError)}</p>
    {/if}
    <div class="flex justify-end gap-2">
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm text-text"
        onclick={() => (open = false)}>{t("common.cancel")}</button
      >
      <Button
        variant="secondary"
        formaction="?/previewPriceIncrease"
        loading={busy.is("preview")}
        disabled={busy.active}
      >
        {t("domains.price_increase.preview")}
      </Button>
      <Button formaction="?/applyPriceIncrease" loading={busy.is("apply")} disabled={busy.active}>
        {t("domains.price_increase.apply")}
      </Button>
    </div>
  </form>
</Modal>
