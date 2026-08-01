<script lang="ts">
  import { Pencil, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import FormCheckbox from "$lib/core/ui/FormCheckbox.svelte";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import I18nTextField from "$lib/core/ui/I18nTextField.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import NumberFormatField from "$lib/core/ui/NumberFormatField.svelte";
  import PhoneInput from "$lib/core/ui/PhoneInput.svelte";
  import { getCurrency } from "$lib/core/currency";
  import TemplateEditor from "$lib/modules/invoicing/TemplateEditor.svelte";
  import { layoutForApi, toConfig } from "$lib/modules/invoicing/templateConfig";
  import type { TemplateConfig } from "$lib/modules/invoicing/templateConfig";
  import { docMoney, taxRateLabel } from "$lib/modules/invoicing/types";

  let { data, form } = $props();

  const busy = new InFlight();

  type TaxRate = (typeof data.taxRates)[number];
  type Template = (typeof data.templates)[number];

  const seller = $derived((data.settings?.company_details ?? {}) as Record<string, string | null>);
  const CATEGORIES = ["standard", "reduced", "zero", "exempt", "reverse_charge"] as const;

  // --- tax rate dialog ------------------------------------------------------- #
  let rateOpen = $state(false);
  let editingRate = $state<TaxRate | null>(null);
  let deleteRateId = $state("");
  let confirmDeleteRate = $state(false);
  function openRate(rate: TaxRate | null) {
    editingRate = rate;
    rateOpen = true;
  }

  // --- product dialog (owner request): default line presets ------------------- #
  type Product = (typeof data.products)[number];
  let productOpen = $state(false);
  let editingProduct = $state<Product | null>(null);
  let deleteProductId = $state("");
  let confirmDeleteProduct = $state(false);
  function openProduct(product: Product | null) {
    editingProduct = product;
    productOpen = true;
  }

  // --- template dialog --------------------------------------------------------- #
  // The whole design is one object now, edited by `TemplateEditor` and posted as JSON. It
  // used to be a handful of `tpl*` scalars mirrored into a hand-built preview document; the
  // preview is the API's real renderer today, so there is nothing left for them to mirror.
  let templateOpen = $state(false);
  let editingTemplate = $state<Template | null>(null);
  let deleteTemplateId = $state("");
  let confirmDeleteTemplate = $state(false);
  let tplName = $state("");
  let tplDefault = $state(false);
  let tplConfig = $state<TemplateConfig>(toConfig({}));

  function openTemplate(template: Template | null) {
    editingTemplate = template;
    tplName = template?.name ?? "";
    tplDefault = template?.is_default ?? false;
    tplConfig = toConfig(template?.config);
    templateOpen = true;
  }

  // `layout` goes over the wire without the editor's own bookkeeping (`locked`, `region`),
  // which the API re-reads from its catalog anyway — and would reject as extra keys.
  const tplConfigJson = $derived(
    JSON.stringify({ ...tplConfig, layout: layoutForApi(tplConfig.layout) }),
  );

  // Bound so the format fields can preview what they will produce (#77) rather than describing
  // their tokens in prose — the hint they replaced said what {seq:4} means, never what you get.
  let invoiceFormat = $state(data.settings?.invoice_number_format ?? "");
  let quoteFormat = $state(data.settings?.quote_number_format ?? "");

  const inputClass =
    "w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
  const sectionClass = "rounded-xl border border-border bg-surface-raised p-5";
</script>

<svelte:head>
  <title>{pageTitle(t("settings.invoicing.title"))}</title>
</svelte:head>

<div class="mb-6">
  <h1 class="mt-1 text-xl font-semibold text-text">{t("settings.invoicing.title")}</h1>
  <p class="text-sm text-text-muted">{t("settings.invoicing.subtitle")}</p>
</div>

{#if form?.saved || form?.rateSaved || form?.templateSaved}
  <p class="mb-4 rounded-lg border border-border bg-surface-raised px-4 py-2 text-sm text-text">
    {t("settings.invoicing.saved")}
  </p>
{/if}
{#if form?.error}
  <p class="mb-4 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
{/if}

<div class="max-w-3xl space-y-6">
  <!-- Seller identity: what every document and the UBL export says about the agency. -->
  <section class={sectionClass}>
    <h2 class="text-base font-semibold text-text">{t("settings.invoicing.seller_heading")}</h2>
    <p class="mb-4 text-sm text-text-muted">{t("settings.invoicing.seller_hint")}</p>
    <form
      method="POST"
      action="?/saveSeller"
      use:enhance={busy.keep("seller")}
      class="grid gap-3 sm:grid-cols-2"
    >
      <div class="sm:col-span-2">
        <label for="seller-name" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.invoicing.name")}</label
        >
        <input id="seller-name" name="name" value={seller.name ?? ""} class={inputClass} />
      </div>
      <div>
        <label for="seller-a1" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.invoicing.address")}</label
        >
        <input
          id="seller-a1"
          name="address_line1"
          value={seller.address_line1 ?? ""}
          class={inputClass}
        />
      </div>
      <div>
        <label for="seller-a2" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.invoicing.address2")}</label
        >
        <input
          id="seller-a2"
          name="address_line2"
          value={seller.address_line2 ?? ""}
          class={inputClass}
        />
      </div>
      <div>
        <label for="seller-zip" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.invoicing.postal_code")}</label
        >
        <input
          id="seller-zip"
          name="postal_code"
          value={seller.postal_code ?? ""}
          class={inputClass}
        />
      </div>
      <div>
        <label for="seller-city" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.invoicing.city")}</label
        >
        <input id="seller-city" name="city" value={seller.city ?? ""} class={inputClass} />
      </div>
      <div>
        <label for="seller-country" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.invoicing.country")}</label
        >
        <input
          id="seller-country"
          name="country"
          maxlength="2"
          value={seller.country ?? ""}
          class={inputClass}
        />
      </div>
      <div>
        <label for="seller-taxcountry" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.invoicing.tax_country")}</label
        >
        <input
          id="seller-taxcountry"
          name="tax_country"
          maxlength="2"
          value={data.settings?.tax_country ?? "NL"}
          class={inputClass}
        />
      </div>
      <div>
        <label for="seller-vat" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.invoicing.vat_number")}</label
        >
        <input
          id="seller-vat"
          name="vat_number"
          value={seller.vat_number ?? ""}
          class={inputClass}
        />
      </div>
      <div>
        <label for="seller-coc" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.invoicing.coc_number")}</label
        >
        <input
          id="seller-coc"
          name="coc_number"
          value={seller.coc_number ?? ""}
          class={inputClass}
        />
      </div>
      <div>
        <label for="seller-iban" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.invoicing.iban")}</label
        >
        <input id="seller-iban" name="iban" value={seller.iban ?? ""} class={inputClass} />
      </div>
      <!-- BIC and website print only on a template whose layout asks for them (both are off
           by default): a SEPA invoice needs no BIC, an international one often does. -->
      <div>
        <label for="seller-bic" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.invoicing.bic")}</label
        >
        <input id="seller-bic" name="bic" value={seller.bic ?? ""} class={inputClass} />
      </div>
      <div>
        <label for="seller-website" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.invoicing.website")}</label
        >
        <input id="seller-website" name="website" value={seller.website ?? ""} class={inputClass} />
      </div>
      <div>
        <label for="seller-email" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.invoicing.email")}</label
        >
        <input
          id="seller-email"
          name="email"
          type="email"
          value={seller.email ?? ""}
          class={inputClass}
        />
      </div>
      <div>
        <label for="seller-phone" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.invoicing.phone")}</label
        >
        <PhoneInput id="seller-phone" name="phone" value={seller.phone ?? ""} />
      </div>
      <div class="sm:col-span-2 flex justify-end">
        <Button loading={busy.is("seller")} disabled={busy.active}>{t("common.save")}</Button>
      </div>
    </form>
  </section>

  <!-- Tax rates: seeded per country, tenant-owned thereafter. -->
  <section class={sectionClass}>
    <div class="mb-1 flex items-center justify-between gap-3">
      <h2 class="text-base font-semibold text-text">{t("settings.invoicing.tax_heading")}</h2>
      <button
        class="rounded-lg bg-brand px-3 py-1.5 text-sm font-medium text-white"
        onclick={() => openRate(null)}>{t("settings.invoicing.new_rate")}</button
      >
    </div>
    <p class="mb-3 text-sm text-text-muted">{t("settings.invoicing.tax_hint")}</p>
    <ul class="divide-y divide-border">
      {#each data.taxRates as rate (rate.id)}
        <li class="flex items-center justify-between gap-3 py-2 text-sm">
          <div class="min-w-0">
            <span class="font-medium {rate.active ? 'text-text' : 'text-text-muted line-through'}"
              >{taxRateLabel(rate, data.locale)}</span
            >
            <span class="ml-2 text-xs text-text-muted">
              {Number(rate.rate)}% · {t(`settings.invoicing.category.${rate.category}`)}
              {#if rate.ledger_code}· {rate.ledger_code}{/if}
            </span>
            {#if rate.is_default}
              <span class="ml-2 rounded-md bg-brand/10 px-1.5 py-0.5 text-xs text-brand"
                >{t("settings.invoicing.default")}</span
              >
            {/if}
          </div>
          <ActionsMenu
            compact
            items={[
              { label: t("common.edit"), icon: Pencil, onclick: () => openRate(rate) },
              {
                label: rate.active ? t("common.deactivate") : t("common.activate"),
                onclick: () => {
                  const formEl = document.getElementById(`toggle-rate-${rate.id}`);
                  (formEl as HTMLFormElement | null)?.requestSubmit();
                },
              },
              {
                label: t("common.delete"),
                icon: Trash2,
                danger: true,
                onclick: () => {
                  deleteRateId = rate.id;
                  confirmDeleteRate = true;
                },
              },
            ]}
          />
          <form
            id="toggle-rate-{rate.id}"
            method="POST"
            action="?/toggleRate"
            use:enhance
            class="hidden"
          >
            <input type="hidden" name="id" value={rate.id} />
            <input type="hidden" name="active" value={rate.active ? "0" : "1"} />
          </form>
        </li>
      {/each}
    </ul>
  </section>

  <!-- Default products (owner request): named line presets for the editors. -->
  <section class={sectionClass}>
    <div class="mb-1 flex items-center justify-between gap-3">
      <h2 class="text-base font-semibold text-text">
        {t("settings.invoicing.products_heading")}
      </h2>
      <button
        class="rounded-lg bg-brand px-3 py-1.5 text-sm font-medium text-white"
        onclick={() => openProduct(null)}>{t("settings.invoicing.new_product")}</button
      >
    </div>
    <p class="mb-3 text-sm text-text-muted">{t("settings.invoicing.products_hint")}</p>
    {#if data.products.length === 0}
      <p class="py-2 text-sm text-text-muted">{t("settings.invoicing.products_empty")}</p>
    {:else}
      <ul class="divide-y divide-border">
        {#each data.products as product (product.id)}
          <li class="flex items-center justify-between gap-3 py-2 text-sm">
            <div class="min-w-0">
              <span
                class="font-medium {product.active ? 'text-text' : 'text-text-muted line-through'}"
                >{product.name}</span
              >
              <span class="ml-2 text-xs text-text-muted">
                {docMoney(Number(product.unit_price), getCurrency(), data.locale)}
                {#if product.unit}/ {product.unit}{/if}
              </span>
            </div>
            <ActionsMenu
              compact
              items={[
                { label: t("common.edit"), icon: Pencil, onclick: () => openProduct(product) },
                {
                  label: product.active ? t("common.deactivate") : t("common.activate"),
                  onclick: () => {
                    const formEl = document.getElementById(`toggle-product-${product.id}`);
                    (formEl as HTMLFormElement | null)?.requestSubmit();
                  },
                },
                {
                  label: t("common.delete"),
                  icon: Trash2,
                  danger: true,
                  onclick: () => {
                    deleteProductId = product.id;
                    confirmDeleteProduct = true;
                  },
                },
              ]}
            />
            <form
              id="toggle-product-{product.id}"
              method="POST"
              action="?/toggleProduct"
              use:enhance
              class="hidden"
            >
              <input type="hidden" name="id" value={product.id} />
              <input type="hidden" name="active" value={product.active ? "0" : "1"} />
            </form>
          </li>
        {/each}
      </ul>
    {/if}
  </section>

  <!-- Templates: designs with a live preview. -->
  <section class={sectionClass}>
    <div class="mb-1 flex items-center justify-between gap-3">
      <h2 class="text-base font-semibold text-text">
        {t("settings.invoicing.templates_heading")}
      </h2>
      <button
        class="rounded-lg bg-brand px-3 py-1.5 text-sm font-medium text-white"
        onclick={() => openTemplate(null)}>{t("settings.invoicing.new_template")}</button
      >
    </div>
    <p class="mb-3 text-sm text-text-muted">{t("settings.invoicing.templates_hint")}</p>
    <ul class="divide-y divide-border">
      {#each data.templates as template (template.id)}
        <li class="flex items-center justify-between gap-3 py-2 text-sm">
          <div>
            <span
              class="font-medium {template.active ? 'text-text' : 'text-text-muted line-through'}"
              >{template.name}</span
            >
            {#if template.is_default}
              <span class="ml-2 rounded-md bg-brand/10 px-1.5 py-0.5 text-xs text-brand"
                >{t("settings.invoicing.default")}</span
              >
            {/if}
          </div>
          <ActionsMenu
            compact
            items={[
              { label: t("common.edit"), icon: Pencil, onclick: () => openTemplate(template) },
              {
                label: t("common.delete"),
                icon: Trash2,
                danger: true,
                onclick: () => {
                  deleteTemplateId = template.id;
                  confirmDeleteTemplate = true;
                },
              },
            ]}
          />
        </li>
      {/each}
    </ul>
  </section>

  <!-- Numbering + document defaults: one form, one save. -->
  <section class={sectionClass}>
    <h2 class="mb-3 text-base font-semibold text-text">
      {t("settings.invoicing.numbering_heading")} · {t("settings.invoicing.defaults_heading")}
    </h2>
    <form
      method="POST"
      action="?/saveDefaults"
      use:enhance={busy.keep("defaults")}
      class="grid gap-3 sm:grid-cols-2"
    >
      <NumberFormatField
        id="fmt-invoice"
        name="invoice_number_format"
        label={t("settings.invoicing.invoice_format")}
        bind:value={invoiceFormat}
        nextSeq={data.settings?.invoice_next_seq ?? 1}
        class={inputClass}
      />
      <NumberFormatField
        id="fmt-quote"
        name="quote_number_format"
        label={t("settings.invoicing.quote_format")}
        bind:value={quoteFormat}
        nextSeq={data.settings?.quote_next_seq ?? 1}
        class={inputClass}
      />
      <div>
        <label for="seq-invoice" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.invoicing.next_invoice_seq")}</label
        >
        <input
          id="seq-invoice"
          name="invoice_next_seq"
          type="number"
          min="1"
          value={data.settings?.invoice_next_seq ?? 1}
          class={inputClass}
        />
      </div>
      <div>
        <label for="seq-quote" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.invoicing.next_quote_seq")}</label
        >
        <input
          id="seq-quote"
          name="quote_next_seq"
          type="number"
          min="1"
          value={data.settings?.quote_next_seq ?? 1}
          class={inputClass}
        />
      </div>
      <label class="flex items-center gap-2 text-sm text-text sm:col-span-2">
        <FormCheckbox
          name="number_reset_yearly"
          value="1"
          checked={data.settings?.number_reset_yearly ?? true}
          class="rounded border-border"
        />
        {t("settings.invoicing.reset_yearly")}
      </label>
      <div>
        <label for="due-days" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.invoicing.due_days")}</label
        >
        <input
          id="due-days"
          name="default_due_days"
          type="number"
          min="0"
          value={data.settings?.default_due_days ?? 14}
          class={inputClass}
        />
      </div>
      <div>
        <label for="valid-days" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.invoicing.quote_valid_days")}</label
        >
        <input
          id="valid-days"
          name="quote_valid_days"
          type="number"
          min="1"
          value={data.settings?.quote_valid_days ?? 30}
          class={inputClass}
        />
      </div>
      <div>
        <label for="default-rate" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.invoicing.default_tax_rate")}</label
        >
        <select id="default-rate" name="default_tax_rate_id" class={inputClass}>
          <option value="">—</option>
          {#each data.taxRates.filter((r) => r.active) as rate (rate.id)}
            <option value={rate.id} selected={data.settings?.default_tax_rate_id === rate.id}
              >{taxRateLabel(rate, data.locale)}</option
            >
          {/each}
        </select>
      </div>
      <div>
        <label for="default-template" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.invoicing.default_template")}</label
        >
        <select id="default-template" name="default_template_id" class={inputClass}>
          <option value="">—</option>
          {#each data.templates.filter((tpl) => tpl.active) as template (template.id)}
            <option
              value={template.id}
              selected={data.settings?.default_template_id === template.id}>{template.name}</option
            >
          {/each}
        </select>
      </div>
      <div>
        <label for="default-rate-hour" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.invoicing.default_hourly_rate")}</label
        >
        <input
          id="default-rate-hour"
          name="default_hourly_rate"
          type="number"
          min="0"
          step="0.01"
          value={data.settings?.default_hourly_rate ?? ""}
          class={inputClass}
        />
      </div>
      <div class="flex items-end">
        <label class="flex items-center gap-2 text-sm text-text">
          <FormCheckbox
            name="prices_include_tax"
            value="1"
            checked={data.settings?.prices_include_tax ?? false}
            class="rounded border-border"
          />
          {t("settings.invoicing.prices_include_tax")}
        </label>
      </div>
      <p class="text-xs text-text-muted sm:col-span-2">
        {t("settings.invoicing.prices_include_tax_hint")}
      </p>
      <div class="flex justify-end sm:col-span-2">
        <Button loading={busy.is("defaults")} disabled={busy.active}>{t("common.save")}</Button>
      </div>
    </form>
  </section>

  <!-- Automatic payment reminders (issue #207): opt-in, tenant schedule. -->
  <section class={sectionClass}>
    <h2 class="mb-1 text-base font-semibold text-text">
      {t("settings.invoicing.reminders_heading")}
    </h2>
    <p class="mb-3 text-sm text-text-muted">{t("settings.invoicing.reminders_hint")}</p>
    <form
      method="POST"
      action="?/saveReminders"
      use:enhance={busy.keep("reminders")}
      class="space-y-3"
    >
      <label class="flex items-center gap-2 text-sm text-text">
        <FormCheckbox
          name="reminders_enabled"
          value="1"
          checked={data.settings?.reminders_enabled ?? false}
          class="rounded border-border"
        />
        {t("settings.invoicing.reminders_enabled")}
      </label>
      <div class="max-w-xs">
        <label for="reminder-days" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.invoicing.reminder_days")}</label
        >
        <input
          id="reminder-days"
          name="reminder_days"
          value={(data.settings?.reminder_days ?? []).join(", ")}
          class={inputClass}
        />
        <p class="mt-1 text-xs text-text-muted">{t("settings.invoicing.reminder_days_hint")}</p>
      </div>
      <div class="flex justify-end">
        <Button loading={busy.is("reminders")} disabled={busy.active}>{t("common.save")}</Button>
      </div>
    </form>
  </section>

  <!-- Accounting (#31): UBL today, live providers behind the same seam later. -->
  <section class={sectionClass}>
    <h2 class="mb-1 text-base font-semibold text-text">
      {t("settings.invoicing.accounting_heading")}
    </h2>
    <p class="text-sm text-text-muted">{t("settings.invoicing.accounting_hint")}</p>
    {#if data.providers.length === 0}
      <p class="mt-2 text-sm text-text-muted">{t("settings.invoicing.providers_empty")}</p>
    {:else}
      <ul class="mt-2 space-y-1 text-sm text-text">
        {#each data.providers as provider (provider.key)}
          <li>{provider.label}</li>
        {/each}
      </ul>
    {/if}
  </section>
</div>

<!-- Tax rate dialog -->
<Modal
  bind:open={rateOpen}
  title={editingRate ? t("settings.invoicing.edit_rate") : t("settings.invoicing.new_rate")}
>
  {#key editingRate?.id ?? "new"}
    <form
      method="POST"
      action="?/saveRate"
      use:enhance={busy.wrap("rate", () => ({ result, update }) => {
        if (result.type === "success") rateOpen = false;
        void update({ reset: false });
      })}
      class="space-y-3"
    >
      {#if editingRate}<input type="hidden" name="id" value={editingRate.id} />{/if}
      <div class="grid gap-3 sm:grid-cols-2">
        <div class="sm:col-span-2">
          {#key editingRate?.id ?? "new"}
            <I18nTextField
              label={t("common.label_field")}
              basename="label"
              values={(editingRate?.label_i18n as Record<string, string> | undefined) ?? {}}
              idPrefix="rate"
            />
          {/key}
        </div>
        <div>
          <label for="rate-pct" class="mb-1 block text-sm font-medium text-text"
            >{t("settings.invoicing.rate")}</label
          >
          <input
            id="rate-pct"
            name="rate"
            type="number"
            min="0"
            max="100"
            step="0.01"
            required
            value={editingRate ? Number(editingRate.rate) : ""}
            class={inputClass}
          />
        </div>
        <div>
          <label for="rate-category" class="mb-1 block text-sm font-medium text-text"
            >{t("settings.invoicing.category")}</label
          >
          <select id="rate-category" name="category" class={inputClass}>
            {#each CATEGORIES as category (category)}
              <option value={category} selected={(editingRate?.category ?? "standard") === category}
                >{t(`settings.invoicing.category.${category}`)}</option
              >
            {/each}
          </select>
        </div>
        <div>
          <label for="rate-ledger" class="mb-1 block text-sm font-medium text-text"
            >{t("settings.invoicing.ledger_code")}</label
          >
          <input
            id="rate-ledger"
            name="ledger_code"
            value={editingRate?.ledger_code ?? ""}
            class={inputClass}
          />
          <p class="mt-1 text-xs text-text-muted">{t("settings.invoicing.ledger_code_hint")}</p>
        </div>
        <div class="flex items-end">
          <label class="flex items-center gap-2 text-sm text-text">
            <FormCheckbox
              name="is_default"
              value="1"
              checked={editingRate?.is_default ?? false}
              class="rounded border-border"
            />
            {t("settings.invoicing.default")}
          </label>
        </div>
      </div>
      {#if form?.error}
        <p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
      {/if}
      <div class="flex justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm text-text"
          onclick={() => (rateOpen = false)}>{t("common.cancel")}</button
        >
        <Button loading={busy.is("rate")} disabled={busy.active}>{t("common.save")}</Button>
      </div>
    </form>
  {/key}
</Modal>

<!-- Template dialog: the editor and the API's own renderer, side by side. -->
<Modal
  bind:open={templateOpen}
  title={editingTemplate
    ? t("settings.invoicing.edit_template")
    : t("settings.invoicing.new_template")}
  size="5xl"
>
  <form
    method="POST"
    action="?/saveTemplate"
    use:enhance={busy.wrap("template", () => ({ result, update }) => {
      if (result.type === "success") templateOpen = false;
      // keep(): the dialog stays open on a validation failure, and blanking the design the
      // author just wrote is the one thing worse than the error.
      void update({ reset: false });
    })}
    class="space-y-4"
  >
    {#if editingTemplate}<input type="hidden" name="id" value={editingTemplate.id} />{/if}
    <input type="hidden" name="config" value={tplConfigJson} />
    <div class="flex flex-wrap items-end gap-4">
      <div class="min-w-56 flex-1">
        <label for="tpl-name" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.invoicing.template_name")}</label
        >
        <input id="tpl-name" name="name" required bind:value={tplName} class={inputClass} />
      </div>
      <label class="flex items-center gap-2 pb-2 text-sm text-text">
        <input
          type="checkbox"
          name="is_default"
          value="1"
          bind:checked={tplDefault}
          class="rounded border-border"
        />
        {t("settings.invoicing.default")}
      </label>
    </div>

    <TemplateEditor
      bind:config={tplConfig}
      catalog={data.blockCatalog}
      canAuthor={data.canAuthorTemplates}
    />

    {#if form?.error}
      <p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
    {/if}
    <div class="flex justify-end gap-2">
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm text-text"
        onclick={() => (templateOpen = false)}>{t("common.cancel")}</button
      >
      <Button loading={busy.is("template")} disabled={busy.active}>{t("common.save")}</Button>
    </div>
  </form>
</Modal>

<Modal
  bind:open={productOpen}
  title={editingProduct
    ? t("settings.invoicing.edit_product")
    : t("settings.invoicing.new_product")}
>
  {#key editingProduct?.id ?? "new"}
    <form
      method="POST"
      action="?/saveProduct"
      use:enhance={busy.wrap("product", () => ({ result, update }) => {
        if (result.type === "success") productOpen = false;
        void update({ reset: false });
      })}
      class="space-y-3"
    >
      {#if editingProduct}<input type="hidden" name="id" value={editingProduct.id} />{/if}
      <div>
        <label for="product-name" class="mb-1 block text-sm font-medium text-text"
          >{t("common.name_field")}</label
        >
        <input
          id="product-name"
          name="name"
          required
          maxlength="255"
          value={editingProduct?.name ?? ""}
          class={inputClass}
        />
      </div>
      <div>
        <label for="product-description" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.invoicing.product_description")}</label
        >
        <textarea
          id="product-description"
          name="description"
          rows="2"
          class={inputClass}
          placeholder={t("settings.invoicing.product_description_hint")}
          >{editingProduct?.description ?? ""}</textarea
        >
      </div>
      <div class="grid gap-3 sm:grid-cols-3">
        <div>
          <label for="product-price" class="mb-1 block text-sm font-medium text-text"
            >{t("invoicing.line.unit_price")}</label
          >
          <input
            id="product-price"
            name="unit_price"
            type="number"
            step="0.01"
            min="0"
            required
            value={editingProduct ? Number(editingProduct.unit_price) : ""}
            class={inputClass}
          />
        </div>
        <div>
          <label for="product-unit" class="mb-1 block text-sm font-medium text-text"
            >{t("invoicing.line.unit")}</label
          >
          <input
            id="product-unit"
            name="unit"
            maxlength="20"
            value={editingProduct?.unit ?? ""}
            placeholder="uur / stuk / maand"
            class={inputClass}
          />
        </div>
        <div>
          <label for="product-tax" class="mb-1 block text-sm font-medium text-text"
            >{t("invoicing.line.tax")}</label
          >
          <select id="product-tax" name="tax_rate_id" class={inputClass}>
            <option value="">—</option>
            {#each data.taxRates.filter((r) => r.active) as rate (rate.id)}
              <option value={rate.id} selected={editingProduct?.tax_rate_id === rate.id}
                >{taxRateLabel(rate, data.locale)}</option
              >
            {/each}
          </select>
        </div>
      </div>
      {#if form?.error}<p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>{/if}
      <div class="flex justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm text-text"
          onclick={() => (productOpen = false)}>{t("common.cancel")}</button
        >
        <Button loading={busy.is("product")} disabled={busy.active}>{t("common.save")}</Button>
      </div>
    </form>
  {/key}
</Modal>

<ConfirmDialog
  bind:open={confirmDeleteProduct}
  title={t("common.delete")}
  message={t("settings.invoicing.delete_product_confirm")}
  action="?/deleteProduct"
  fields={{ id: deleteProductId }}
/>
<ConfirmDialog
  bind:open={confirmDeleteRate}
  title={t("common.delete")}
  message={t("settings.invoicing.delete_rate_confirm")}
  action="?/deleteRate"
  fields={{ id: deleteRateId }}
/>
<ConfirmDialog
  bind:open={confirmDeleteTemplate}
  title={t("common.delete")}
  message={t("settings.invoicing.delete_template_confirm")}
  action="?/deleteTemplate"
  fields={{ id: deleteTemplateId }}
/>
