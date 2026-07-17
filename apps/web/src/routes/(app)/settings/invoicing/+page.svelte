<script lang="ts">
  import { Pencil, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import FormCheckbox from "$lib/core/ui/FormCheckbox.svelte";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import I18nTextField from "$lib/core/ui/I18nTextField.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import DocumentView from "$lib/modules/invoicing/DocumentView.svelte";
  import { taxRateLabel } from "$lib/modules/invoicing/types";
  import { page } from "$app/state";

  let { data, form } = $props();

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

  // --- template dialog with live preview -------------------------------------- #
  let templateOpen = $state(false);
  let editingTemplate = $state<Template | null>(null);
  let deleteTemplateId = $state("");
  let confirmDeleteTemplate = $state(false);
  let tplName = $state("");
  let tplAccent = $state("");
  let tplShowLogo = $state(true);
  let tplColumns = $state({ quantity: true, unit: false, unit_price: true, tax: true });
  let tplTexts = $state<Record<string, Record<string, string>>>({
    intro_i18n: { nl: "", en: "" },
    payment_i18n: { nl: "", en: "" },
    footer_i18n: { nl: "", en: "" },
  });
  let tplDefault = $state(false);
  let tplPreviewLocale = $state("nl");

  function openTemplate(template: Template | null) {
    editingTemplate = template;
    const config = (template?.config ?? {}) as {
      accent_color?: string | null;
      show_logo?: boolean;
      columns?: Partial<Record<"quantity" | "unit" | "unit_price" | "tax", boolean>>;
      intro_i18n?: Record<string, string>;
      payment_i18n?: Record<string, string>;
      footer_i18n?: Record<string, string>;
    };
    tplName = template?.name ?? "";
    tplAccent = config.accent_color ?? "";
    tplShowLogo = config.show_logo !== false;
    tplColumns = {
      quantity: true,
      unit: false,
      unit_price: true,
      tax: true,
      ...(config.columns ?? {}),
    };
    tplTexts = {
      intro_i18n: { nl: "", en: "", ...(config.intro_i18n ?? {}) },
      payment_i18n: { nl: "", en: "", ...(config.payment_i18n ?? {}) },
      footer_i18n: { nl: "", en: "", ...(config.footer_i18n ?? {}) },
    };
    tplDefault = template?.is_default ?? false;
    templateOpen = true;
  }
  const tplConfigJson = $derived(
    JSON.stringify({
      accent_color: tplAccent || null,
      show_logo: tplShowLogo,
      columns: tplColumns,
      intro_i18n: tplTexts.intro_i18n,
      payment_i18n: tplTexts.payment_i18n,
      footer_i18n: tplTexts.footer_i18n,
    }),
  );
  // A sample document so the designer sees the config live, before saving.
  const previewDoc = $derived({
    id: "preview",
    number: "2026-0042",
    status: "open",
    locale: tplPreviewLocale,
    currency: "EUR",
    issue_date: "2026-07-01",
    due_date: "2026-07-15",
    reference: "PO-123",
    intro: null,
    notes: null,
    customer: {
      name: "Klant BV",
      address_line1: "Dorpsstraat 1",
      postal_code: "1234 AB",
      city: "Utrecht",
    },
    subtotal: "1000.00",
    tax_total: "210.00",
    total: "1210.00",
    paid_total: "0.00",
    outstanding: "1210.00",
    lines: [
      {
        id: "l1",
        position: 0,
        description: t("settings.invoicing.preview"),
        quantity: "10.00",
        unit: "uur",
        unit_price: "100.00",
        tax_rate_id: null,
        tax_rate_pct: "21.00",
        tax_name: "21%",
        tax_category: "standard",
        amount: "1000.00",
      },
    ],
    tax_groups: [
      { rate_pct: "21.00", category: "standard", name: "21%", base: "1000.00", tax: "210.00" },
    ],
  });
  const previewTemplate = $derived({
    id: "preview",
    name: tplName,
    config: JSON.parse(tplConfigJson),
    is_default: false,
    active: true,
    position: 0,
  });

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
    <form method="POST" action="?/saveSeller" use:enhance class="grid gap-3 sm:grid-cols-2">
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
        <input id="seller-phone" name="phone" value={seller.phone ?? ""} class={inputClass} />
      </div>
      <div class="sm:col-span-2 flex justify-end">
        <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white"
          >{t("common.save")}</button
        >
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
    <form method="POST" action="?/saveDefaults" use:enhance class="grid gap-3 sm:grid-cols-2">
      <div>
        <label for="fmt-invoice" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.invoicing.invoice_format")}</label
        >
        <input
          id="fmt-invoice"
          name="invoice_number_format"
          value={data.settings?.invoice_number_format ?? ""}
          class={inputClass}
        />
      </div>
      <div>
        <label for="fmt-quote" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.invoicing.quote_format")}</label
        >
        <input
          id="fmt-quote"
          name="quote_number_format"
          value={data.settings?.quote_number_format ?? ""}
          class={inputClass}
        />
      </div>
      <p class="text-xs text-text-muted sm:col-span-2">
        {t("settings.invoicing.format_hint", {
          tokens: "{year}, {yy}, {seq}, {seq:4}",
          example: "F{year}-{seq:4} → F2026-0001",
        })}
      </p>
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
        <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white"
          >{t("common.save")}</button
        >
      </div>
    </form>
  </section>

  <!-- Automatic payment reminders (issue #207): opt-in, tenant schedule. -->
  <section class={sectionClass}>
    <h2 class="mb-1 text-base font-semibold text-text">
      {t("settings.invoicing.reminders_heading")}
    </h2>
    <p class="mb-3 text-sm text-text-muted">{t("settings.invoicing.reminders_hint")}</p>
    <form method="POST" action="?/saveReminders" use:enhance class="space-y-3">
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
        <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white"
          >{t("common.save")}</button
        >
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
      use:enhance={() =>
        ({ result, update }) => {
          if (result.type === "success") rateOpen = false;
          void update({ reset: false });
        }}
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
        <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white"
          >{t("common.save")}</button
        >
      </div>
    </form>
  {/key}
</Modal>

<!-- Template dialog with live preview -->
<Modal
  bind:open={templateOpen}
  title={editingTemplate
    ? t("settings.invoicing.edit_template")
    : t("settings.invoicing.new_template")}
  size="3xl"
>
  <div class="grid gap-6 lg:grid-cols-[22rem_1fr]">
    <form
      method="POST"
      action="?/saveTemplate"
      use:enhance={() =>
        ({ result, update }) => {
          if (result.type === "success") templateOpen = false;
          void update({ reset: false });
        }}
      class="space-y-3"
    >
      {#if editingTemplate}<input type="hidden" name="id" value={editingTemplate.id} />{/if}
      <input type="hidden" name="config" value={tplConfigJson} />
      <div>
        <label for="tpl-name" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.invoicing.template_name")}</label
        >
        <input id="tpl-name" name="name" required bind:value={tplName} class={inputClass} />
      </div>
      <div>
        <label for="tpl-accent" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.invoicing.accent_color")}</label
        >
        <input id="tpl-accent" bind:value={tplAccent} placeholder="#4f46e5" class={inputClass} />
        <p class="mt-1 text-xs text-text-muted">{t("settings.invoicing.accent_color_hint")}</p>
      </div>
      <label class="flex items-center gap-2 text-sm text-text">
        <input type="checkbox" bind:checked={tplShowLogo} class="rounded border-border" />
        {t("settings.invoicing.show_logo")}
      </label>
      <fieldset>
        <legend class="mb-1 text-sm font-medium text-text"
          >{t("settings.invoicing.columns_heading")}</legend
        >
        <div class="grid grid-cols-2 gap-1">
          <label class="flex items-center gap-2 text-sm text-text">
            <input
              type="checkbox"
              bind:checked={tplColumns.quantity}
              class="rounded border-border"
            />
            {t("settings.invoicing.col_quantity")}
          </label>
          <label class="flex items-center gap-2 text-sm text-text">
            <input type="checkbox" bind:checked={tplColumns.unit} class="rounded border-border" />
            {t("settings.invoicing.col_unit")}
          </label>
          <label class="flex items-center gap-2 text-sm text-text">
            <input
              type="checkbox"
              bind:checked={tplColumns.unit_price}
              class="rounded border-border"
            />
            {t("settings.invoicing.col_unit_price")}
          </label>
          <label class="flex items-center gap-2 text-sm text-text">
            <input type="checkbox" bind:checked={tplColumns.tax} class="rounded border-border" />
            {t("settings.invoicing.col_tax")}
          </label>
        </div>
      </fieldset>
      {#each ["nl", "en"] as textLocale (textLocale)}
        <div>
          <label for="tpl-intro-{textLocale}" class="mb-1 block text-sm font-medium text-text"
            >{t("settings.invoicing.intro_text", { locale: textLocale })}</label
          >
          <textarea
            id="tpl-intro-{textLocale}"
            rows="2"
            bind:value={tplTexts.intro_i18n[textLocale]}
            class={inputClass}></textarea>
        </div>
        <div>
          <label for="tpl-payment-{textLocale}" class="mb-1 block text-sm font-medium text-text"
            >{t("settings.invoicing.payment_text", { locale: textLocale })}</label
          >
          <textarea
            id="tpl-payment-{textLocale}"
            rows="2"
            bind:value={tplTexts.payment_i18n[textLocale]}
            class={inputClass}></textarea>
        </div>
        <div>
          <label for="tpl-footer-{textLocale}" class="mb-1 block text-sm font-medium text-text"
            >{t("settings.invoicing.footer_text", { locale: textLocale })}</label
          >
          <textarea
            id="tpl-footer-{textLocale}"
            rows="2"
            bind:value={tplTexts.footer_i18n[textLocale]}
            class={inputClass}></textarea>
        </div>
      {/each}
      <label class="flex items-center gap-2 text-sm text-text">
        <input
          type="checkbox"
          name="is_default"
          value="1"
          bind:checked={tplDefault}
          class="rounded border-border"
        />
        {t("settings.invoicing.default")}
      </label>
      {#if form?.error}
        <p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
      {/if}
      <div class="flex justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm text-text"
          onclick={() => (templateOpen = false)}>{t("common.cancel")}</button
        >
        <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white"
          >{t("common.save")}</button
        >
      </div>
    </form>
    <div class="hidden min-w-0 lg:block">
      <div class="mb-2 flex items-center justify-between">
        <p class="text-sm font-medium text-text">{t("settings.invoicing.preview")}</p>
        <select
          bind:value={tplPreviewLocale}
          class="rounded-lg border border-border bg-surface-raised px-2 py-1 text-xs"
          aria-label={t("invoicing.field.locale")}
        >
          <option value="nl">nl</option>
          <option value="en">en</option>
        </select>
      </div>
      <div class="max-h-[70vh] overflow-y-auto rounded-lg border border-border">
        <DocumentView
          doc={previewDoc as never}
          kind="invoice"
          template={previewTemplate as never}
          seller={seller as never}
          brandName={page.data.theme?.brandName ?? ""}
          logoUrl={page.data.theme?.logoUrl ?? null}
          brandColor={page.data.theme?.primaryColor ?? "#4f46e5"}
        />
      </div>
    </div>
  </div>
</Modal>

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
