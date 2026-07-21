<script lang="ts">
  /**
   * The price-increase dialog (#30, #231) plus its applied-notice, shared by the
   * subscriptions list and the standard-subscriptions tab. Scope is one Combobox —
   * everything, one type, one subscription or one template; a row's ⋮ shortcut opens it
   * `locked` to that row, so the field shows read-only. The preview is the API's own
   * computation: the numbers shown are exactly what an apply writes.
   */
  import { enhance } from "$app/forms";
  import { fmtMoney } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";

  interface ScopeItem {
    value: string;
    label: string;
    hint?: string;
  }
  interface PricePreview {
    items: {
      subscription_id: string;
      name: string;
      company_name?: string;
      current_amount: string;
      new_amount: string;
    }[];
    templates: { template_id: string; name: string; current_amount: string; new_amount: string }[];
  }

  let {
    open = $bindable(false),
    scope = $bindable("all"),
    scopeItems,
    locked = false,
    form,
  }: {
    open?: boolean;
    /** `all`, `type:<id>`, `subscription:<id>` or `template:<id>`. */
    scope?: string;
    scopeItems: ScopeItem[];
    /** From a row's ⋮ shortcut: the scope is that row's, shown read-only. */
    locked?: boolean;
    form: {
      pricePreview?: PricePreview | null;
      priceScope?: string | null;
      priceError?: string | null;
      priceApplied?: number | null;
      priceAppliedTemplates?: number | null;
    } | null;
  } = $props();

  let priceMode = $state<"percent" | "amount" | "set">("percent");
  const PRICE_MODES = ["percent", "amount", "set"] as const;

  const scopeLabel = $derived(scopeItems.find((i) => i.value === scope)?.label ?? "");
  // Templates only ride along on the broad scopes; a single row never drags them (#231).
  const scopeTakesTemplates = $derived(scope === "all" || scope.startsWith("type:"));
  // A preview made for another scope (an earlier open) would mislead — render only a match.
  const preview = $derived((form?.priceScope ?? "") === scope ? form?.pricePreview : null);

  const money = (value: string | number | null | undefined) =>
    value == null ? "—" : fmtMoney(Number(value));

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

{#if form?.priceApplied != null}
  <p class="mb-4 rounded-lg border border-border bg-surface-raised px-4 py-2 text-sm text-text">
    {#if form.priceApplied > 0 || !form.priceAppliedTemplates}
      {t("subscriptions.price_increase.applied", { count: form.priceApplied })}
    {/if}
    {#if form.priceAppliedTemplates}
      {t("subscriptions.price_increase.applied_templates", {
        count: form.priceAppliedTemplates,
      })}
    {/if}
  </p>
{/if}

<Modal bind:open title={t("subscriptions.price_increase.title")}>
  <!-- Enter previews (the safe default); only the explicit Doorvoeren button applies. -->
  <form
    method="POST"
    action="?/previewPriceIncrease"
    use:enhance={() =>
      ({ result, update }) => {
        if (result.type === "success" && result.data && "priceApplied" in result.data) {
          open = false;
        }
        void update({ reset: false });
      }}
    class="space-y-4"
  >
    <p class="text-sm text-text-muted">{t("subscriptions.price_increase.help")}</p>
    <div class="grid gap-3 sm:grid-cols-2">
      <div>
        <label for="pi-mode" class="mb-1 block text-sm font-medium text-text"
          >{t("subscriptions.price_increase.mode")}</label
        >
        <select id="pi-mode" name="mode" bind:value={priceMode} class={inputClass}>
          {#each PRICE_MODES as mode (mode)}
            <option value={mode}>{t(`subscriptions.price_increase.mode_${mode}`)}</option>
          {/each}
        </select>
      </div>
      <div>
        <label for="pi-value" class="mb-1 block text-sm font-medium text-text"
          >{priceMode === "percent"
            ? t("subscriptions.price_increase.value_percent")
            : t("subscriptions.price_increase.value_amount")}</label
        >
        <input id="pi-value" name="value" type="number" step="0.01" required class={inputClass} />
      </div>
      <div>
        <label for="pi-from" class="mb-1 block text-sm font-medium text-text"
          >{t("subscriptions.price_increase.valid_from")}</label
        >
        <DateInput name="valid_from" id="pi-from" required value="" />
      </div>
      <div>
        <label for="pi-scope" class="mb-1 block text-sm font-medium text-text"
          >{t("subscriptions.price_increase.scope")}</label
        >
        {#if locked}
          <input
            id="pi-scope"
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
            id="pi-scope"
            placeholder={t("subscriptions.price_increase.scope")}
          />
        {/if}
      </div>
    </div>
    {#if scopeTakesTemplates}
      <label class="flex items-center gap-2 text-sm text-text">
        <input type="checkbox" name="include_templates" />
        {t("subscriptions.price_increase.include_templates")}
      </label>
    {/if}

    {#if preview}
      <div class="max-h-64 overflow-y-auto rounded-lg border border-border">
        {#if preview.items.length === 0 && preview.templates.length === 0}
          <p class="p-4 text-sm text-text-muted">{t("subscriptions.price_increase.empty")}</p>
        {:else}
          <table class="w-full text-sm">
            <thead>
              <tr class="border-b border-border text-left text-xs text-text-muted">
                <th class="px-3 py-2 font-medium">{t("subscriptions.field.name")}</th>
                <th class="px-3 py-2 text-right font-medium"
                  >{t("subscriptions.price_increase.current")}</th
                >
                <th class="px-3 py-2 text-right font-medium"
                  >{t("subscriptions.price_increase.new")}</th
                >
              </tr>
            </thead>
            <tbody>
              {#each preview.items as item (item.subscription_id)}
                <tr class="border-b border-border last:border-b-0">
                  <td class="px-3 py-1.5 text-text">
                    {item.name}
                    {#if item.company_name}<span class="text-xs text-text-muted">
                        · {item.company_name}</span
                      >{/if}
                  </td>
                  <td class="px-3 py-1.5 text-right tabular-nums text-text-muted"
                    >{money(item.current_amount)}</td
                  >
                  <td class="px-3 py-1.5 text-right font-medium tabular-nums text-text"
                    >{money(item.new_amount)}</td
                  >
                </tr>
              {/each}
              {#each preview.templates as tpl (tpl.template_id)}
                <tr class="border-b border-border last:border-b-0">
                  <td class="px-3 py-1.5 text-text">
                    {tpl.name}
                    <span class="text-xs text-text-muted">
                      · {t("settings.subscriptions.templates_heading")}</span
                    >
                  </td>
                  <td class="px-3 py-1.5 text-right tabular-nums text-text-muted"
                    >{money(tpl.current_amount)}</td
                  >
                  <td class="px-3 py-1.5 text-right font-medium tabular-nums text-text"
                    >{money(tpl.new_amount)}</td
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
      <button
        formaction="?/previewPriceIncrease"
        class="rounded-lg border border-border px-4 py-2 text-sm font-medium text-text hover:border-brand hover:text-brand"
        >{t("subscriptions.price_increase.preview")}</button
      >
      <button
        formaction="?/applyPriceIncrease"
        class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white"
        >{t("subscriptions.price_increase.apply")}</button
      >
    </div>
  </form>
</Modal>
