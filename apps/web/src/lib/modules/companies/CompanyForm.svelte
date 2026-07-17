<script lang="ts">
  /**
   * The single definition of a client's fields (docs/UX.md: never a name-only stub form).
   *
   * Renders the field set only — the caller owns the `<form>`, its action and its buttons — so
   * create (clients list), edit (client detail) and the quick-create dialog on a contact page
   * all show exactly the same fields, including the tenant's custom-field definitions.
   * `children` renders after the fields, for surfaces that add their own (e.g. the contact
   * picker on create, which only exists before the client has an id).
   */
  import type { Snippet } from "svelte";

  import CustomFieldsForm from "$lib/core/customfields/CustomFieldsForm.svelte";
  import type { CustomFieldDefinition } from "$lib/core/customfields/types";
  import { t } from "$lib/core/i18n";
  import AssigneePicker from "$lib/core/ui/AssigneePicker.svelte";
  import { COMPANY_STATUSES } from "$lib/modules/companies/status";

  interface Member {
    user_id: string;
    full_name?: string | null;
    email: string;
  }
  interface CompanyValues {
    name?: string;
    website?: string | null;
    invoice_email?: string | null;
    vat_number?: string | null;
    coc_number?: string | null;
    address_line1?: string | null;
    address_line2?: string | null;
    postal_code?: string | null;
    city?: string | null;
    country?: string | null;
    notes?: string | null;
    status?: string | null;
    /** Every employee working this client, the verantwoordelijke starred. Primary first. */
    assignees?: { user_id: string; is_primary: boolean }[];
    custom?: Record<string, unknown> | null;
  }

  let {
    company = {},
    members = [],
    definitions = [],
    locale,
    /** Prefixes the input ids so two instances can coexist on one page. */
    idPrefix = "company",
    children,
  }: {
    company?: CompanyValues;
    members?: Member[];
    definitions?: CustomFieldDefinition[];
    locale: string;
    idPrefix?: string;
    children?: Snippet;
  } = $props();

  const status = $derived(company.status ?? "active");

  const inputClass =
    "w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<div class="space-y-3">
  <div class="grid gap-3 sm:grid-cols-2">
    <div class="sm:col-span-2">
      <label for="{idPrefix}-name" class="mb-1 block text-sm font-medium text-neutral-700">
        {t("companies.name")}
      </label>
      <input
        id="{idPrefix}-name"
        name="name"
        value={company.name ?? ""}
        required
        class={inputClass}
      />
    </div>
    <div>
      <label for="{idPrefix}-website" class="mb-1 block text-sm font-medium text-neutral-700">
        {t("companies.website")}
      </label>
      <input
        id="{idPrefix}-website"
        name="website"
        value={company.website ?? ""}
        placeholder="https://…"
        class={inputClass}
      />
    </div>
    <div>
      <label for="{idPrefix}-invoice-email" class="mb-1 block text-sm font-medium text-neutral-700">
        {t("companies.invoice_email")}
      </label>
      <input
        id="{idPrefix}-invoice-email"
        name="invoice_email"
        type="email"
        value={company.invoice_email ?? ""}
        placeholder="facturen@…"
        class={inputClass}
      />
    </div>
    <!-- Billing identity (issue #11): what an issued invoice snapshots (#207). -->
    <fieldset class="sm:col-span-2">
      <legend class="mb-1 text-sm font-medium text-neutral-700">
        {t("companies.billing_heading")}
      </legend>
      <div class="grid gap-3 sm:grid-cols-2">
        <div>
          <label for="{idPrefix}-vat" class="mb-1 block text-sm font-medium text-neutral-700">
            {t("companies.vat_number")}
          </label>
          <input
            id="{idPrefix}-vat"
            name="vat_number"
            value={company.vat_number ?? ""}
            class={inputClass}
          />
        </div>
        <div>
          <label for="{idPrefix}-coc" class="mb-1 block text-sm font-medium text-neutral-700">
            {t("companies.coc_number")}
          </label>
          <input
            id="{idPrefix}-coc"
            name="coc_number"
            value={company.coc_number ?? ""}
            class={inputClass}
          />
        </div>
        <div>
          <label for="{idPrefix}-address1" class="mb-1 block text-sm font-medium text-neutral-700">
            {t("companies.address_line1")}
          </label>
          <input
            id="{idPrefix}-address1"
            name="address_line1"
            value={company.address_line1 ?? ""}
            class={inputClass}
          />
        </div>
        <div>
          <label for="{idPrefix}-address2" class="mb-1 block text-sm font-medium text-neutral-700">
            {t("companies.address_line2")}
          </label>
          <input
            id="{idPrefix}-address2"
            name="address_line2"
            value={company.address_line2 ?? ""}
            class={inputClass}
          />
        </div>
        <div>
          <label for="{idPrefix}-zip" class="mb-1 block text-sm font-medium text-neutral-700">
            {t("companies.postal_code")}
          </label>
          <input
            id="{idPrefix}-zip"
            name="postal_code"
            value={company.postal_code ?? ""}
            class={inputClass}
          />
        </div>
        <div>
          <label for="{idPrefix}-city" class="mb-1 block text-sm font-medium text-neutral-700">
            {t("companies.city")}
          </label>
          <input id="{idPrefix}-city" name="city" value={company.city ?? ""} class={inputClass} />
        </div>
        <div>
          <label for="{idPrefix}-country" class="mb-1 block text-sm font-medium text-neutral-700">
            {t("companies.country")}
          </label>
          <input
            id="{idPrefix}-country"
            name="country"
            maxlength="2"
            value={company.country ?? ""}
            class={inputClass}
          />
        </div>
      </div>
    </fieldset>
    <div>
      <label for="{idPrefix}-status" class="mb-1 block text-sm font-medium text-neutral-700">
        {t("companies.field.status")}
      </label>
      <select id="{idPrefix}-status" name="status" class={inputClass}>
        {#each COMPANY_STATUSES as option (option)}
          <option value={option} selected={option === status}>
            {t(`companies.status.${option}`)}
          </option>
        {/each}
      </select>
    </div>
    <div class="sm:col-span-2">
      <span class="mb-1 block text-sm font-medium text-neutral-700">
        {t("companies.field.assignees")}
      </span>
      <AssigneePicker
        {members}
        value={company.assignees ?? []}
        id="{idPrefix}-assignees"
        placeholder={t("assignees.add")}
      />
    </div>
    <div class="sm:col-span-2">
      <label for="{idPrefix}-notes" class="mb-1 block text-sm font-medium text-neutral-700">
        {t("companies.notes")}
      </label>
      <textarea id="{idPrefix}-notes" name="notes" rows="3" class={inputClass}
        >{company.notes ?? ""}</textarea
      >
    </div>
  </div>

  {#if definitions.length > 0}
    <CustomFieldsForm {definitions} values={company.custom ?? {}} {locale} />
  {:else}
    <input type="hidden" name="custom" value={JSON.stringify(company.custom ?? {})} />
  {/if}

  {@render children?.()}

  <p class="text-xs text-neutral-400">{t("companies.status_hint")}</p>
</div>
