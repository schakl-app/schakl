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
  import Combobox from "$lib/core/ui/Combobox.svelte";
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
    notes?: string | null;
    status?: string | null;
    responsible_user_id?: string | null;
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

  const memberItems = $derived(
    members.map((m) => ({ value: m.user_id, label: m.full_name || m.email })),
  );
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
      <label for="{idPrefix}-responsible" class="mb-1 block text-sm font-medium text-neutral-700">
        {t("companies.field.responsible")}
      </label>
      <Combobox
        items={memberItems}
        name="responsible_user_id"
        id="{idPrefix}-responsible"
        value={company.responsible_user_id ?? ""}
        placeholder={t("common.unassigned")}
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
