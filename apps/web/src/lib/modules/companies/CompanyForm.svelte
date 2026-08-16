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
  import Button from "$lib/core/ui/Button.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import CountryInput from "$lib/core/ui/CountryInput.svelte";
  import PhoneInput from "$lib/core/ui/PhoneInput.svelte";
  import RichTextEditor from "$lib/core/ui/RichTextEditor.svelte";
  import { COMPANY_STATUSES } from "$lib/modules/companies/status";

  interface Member {
    user_id: string;
    full_name?: string | null;
    email: string | null;
    is_active?: boolean;
  }
  interface CompanyValues {
    /** Present when editing an existing client; scopes the notes editor's #task candidates. */
    id?: string;
    name?: string;
    /** Klantnummer; blank on create means the org's numbering allocates one. */
    client_number?: string | null;
    website?: string | null;
    phone?: string | null;
    invoice_email?: string | null;
    vat_number?: string | null;
    coc_number?: string | null;
    address_line1?: string | null;
    house_number?: string | null;
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
    section = "all",
    children,
  }: {
    company?: CompanyValues;
    members?: Member[];
    definitions?: CustomFieldDefinition[];
    locale: string;
    idPrefix?: string;
    /**
     * Which coherent group of fields to render (#364).
     *
     * "The size of the edit surface should match the size of the edit, and today there is exactly
     * one size for all of them" — a 512 px dialog rendering 1445 px tall, with Opslaan below the
     * fold, whether you were changing a billing address or filling in everything. So Gegevens and
     * Factuurgegevens each flip *their own card* into edit mode, and only the full record still
     * opens the whole form (docked right, where a long form fits).
     *
     * The reason a partial form is safe is on the server: the update action patches only the
     * fields the form actually carried, so a section save cannot null what it left out.
     */
    section?: "all" | "identity" | "billing" | "extra";
    children?: Snippet;
  } = $props();

  const shows = (part: "identity" | "billing" | "extra") => section === "all" || section === part;

  const status = $derived(company.status ?? "active");

  const inputClass =
    "w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";

  // ---- postcode → address lookup (#241) --------------------------------- //
  // The address fields are bound so an accepted suggestion can fill them; each carries
  // `defaultValue` so a form reset restores the saved value, never blank (docs/UX.md).
  // Street (`address_line1`) and house number are their own columns; a pre-split record
  // still holds "Straatnaam 12" in the street field until someone normalises it here.
  // The initial-value capture is deliberate: `company` never changes while the form is
  // mounted (create passes `{}`, edit passes the loaded record once).
  // svelte-ignore state_referenced_locally
  let addressLine1 = $state(company.address_line1 ?? "");
  // svelte-ignore state_referenced_locally
  let houseNumber = $state(company.house_number ?? "");
  // svelte-ignore state_referenced_locally
  let postalCode = $state(company.postal_code ?? "");
  // svelte-ignore state_referenced_locally
  let city = $state(company.city ?? "");
  // svelte-ignore state_referenced_locally
  let country = $state(company.country ?? "");

  interface AddressSuggestion {
    street: string;
    house_number: string;
    postal_code: string;
    city: string;
    country: string;
  }
  let suggestion = $state<AddressSuggestion | null>(null);
  let lookupTimer: ReturnType<typeof setTimeout> | undefined;
  let lookupSeq = 0;

  // Only a complete Dutch postcode triggers a lookup — the provider (PDOK) covers NL, and
  // half-typed input would just spam requests that answer nothing.
  const NL_POSTCODE = /^[1-9][0-9]{3}\s?[A-Za-z]{2}$/;

  function formatPostal(raw: string): string {
    const bare = raw.replace(/\s/g, "").toUpperCase();
    return bare.length === 6 ? `${bare.slice(0, 4)} ${bare.slice(4)}` : raw;
  }

  function composedLine(s: AddressSuggestion): string {
    return `${s.street} ${s.house_number}`.trim();
  }

  function scheduleLookup() {
    suggestion = null;
    clearTimeout(lookupTimer);
    lookupSeq += 1;
    if (!NL_POSTCODE.test(postalCode.trim()) || !houseNumber.trim()) return;
    lookupTimer = setTimeout(runLookup, 400);
  }

  /** A suggestion is a convenience: any failure (offline, no permission, provider down)
   *  degrades to "no suggestion", never to an error banner. */
  async function runLookup() {
    const seq = lookupSeq;
    const params = new URLSearchParams({
      postal_code: postalCode.trim(),
      house_number: houseNumber.trim(),
    });
    let found: AddressSuggestion | null = null;
    try {
      const response = await fetch(`/api/v1/addresslookup?${params}`, {
        headers: { accept: "application/json" },
      });
      if (response.ok) {
        const data = (await response.json()) as { suggestions?: AddressSuggestion[] };
        found = data.suggestions?.[0] ?? null;
      }
    } catch {
      found = null;
    }
    if (seq !== lookupSeq) return; // the input changed while we were looking
    // What's already filled in needs no "did you mean" — offer only a difference.
    if (
      found &&
      found.street === addressLine1.trim() &&
      found.house_number === houseNumber.trim() &&
      found.city === city.trim()
    ) {
      found = null;
    }
    suggestion = found;
  }

  function applySuggestion() {
    if (!suggestion) return;
    addressLine1 = suggestion.street;
    houseNumber = suggestion.house_number;
    postalCode = formatPostal(suggestion.postal_code);
    city = suggestion.city;
    country = suggestion.country;
    suggestion = null;
  }
</script>

<div class="space-y-3">
  <div class="grid gap-3 sm:grid-cols-2">
    {#if shows("identity")}
      <div class="sm:col-span-2">
        <label for="{idPrefix}-name" class="mb-1 block text-sm font-medium text-text">
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
        <label for="{idPrefix}-client-number" class="mb-1 block text-sm font-medium text-text">
          {t("companies.client_number")}
        </label>
        <input
          id="{idPrefix}-client-number"
          name="client_number"
          value={company.client_number ?? ""}
          placeholder={t("companies.client_number_auto")}
          class={inputClass}
        />
      </div>
      <div>
        <label for="{idPrefix}-website" class="mb-1 block text-sm font-medium text-text">
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
        <label for="{idPrefix}-phone" class="mb-1 block text-sm font-medium text-text">
          {t("companies.phone")}
        </label>
        <PhoneInput id="{idPrefix}-phone" name="phone" value={company.phone ?? ""} />
      </div>
      <div>
        <label for="{idPrefix}-invoice-email" class="mb-1 block text-sm font-medium text-text">
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
      <div class="sm:col-span-2">
        <label for="{idPrefix}-notes" class="mb-1 block text-sm font-medium text-text">
          {t("companies.notes")}
        </label>
        <RichTextEditor
          id="{idPrefix}-notes"
          name="notes"
          rows={3}
          value={company.notes ?? ""}
          scope={{ companyId: company.id ?? null }}
        />
      </div>
    {/if}
    {#if shows("billing")}
      <!-- Billing identity (issue #11): what an issued invoice snapshots (#207). -->
      <fieldset class="sm:col-span-2">
        <legend class="mb-1 text-sm font-medium text-text">
          {t("companies.billing_heading")}
        </legend>
        <div class="grid gap-3 sm:grid-cols-2">
          <div>
            <label for="{idPrefix}-vat" class="mb-1 block text-sm font-medium text-text">
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
            <label for="{idPrefix}-coc" class="mb-1 block text-sm font-medium text-text">
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
            <label for="{idPrefix}-zip" class="mb-1 block text-sm font-medium text-text">
              {t("companies.postal_code")}
            </label>
            <input
              id="{idPrefix}-zip"
              name="postal_code"
              bind:value={postalCode}
              defaultValue={company.postal_code ?? ""}
              oninput={scheduleLookup}
              class={inputClass}
            />
          </div>
          <div>
            <label for="{idPrefix}-house-number" class="mb-1 block text-sm font-medium text-text">
              {t("companies.house_number")}
            </label>
            <input
              id="{idPrefix}-house-number"
              name="house_number"
              bind:value={houseNumber}
              defaultValue={company.house_number ?? ""}
              oninput={scheduleLookup}
              class={inputClass}
            />
          </div>
          {#if suggestion}
            <div
              class="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text sm:col-span-2"
            >
              <span>
                {t("companies.address_lookup.suggestion", {
                  address: `${composedLine(suggestion)}, ${formatPostal(suggestion.postal_code)} ${suggestion.city}`,
                })}
              </span>
              <Button type="button" size="xs" onclick={applySuggestion}>
                {t("companies.address_lookup.apply")}
              </Button>
              <button
                type="button"
                class="text-xs text-text-muted hover:underline"
                onclick={() => (suggestion = null)}
              >
                {t("companies.address_lookup.dismiss")}
              </button>
            </div>
          {/if}
          <div>
            <label for="{idPrefix}-address1" class="mb-1 block text-sm font-medium text-text">
              {t("companies.address_line1")}
            </label>
            <input
              id="{idPrefix}-address1"
              name="address_line1"
              bind:value={addressLine1}
              defaultValue={company.address_line1 ?? ""}
              class={inputClass}
            />
          </div>
          <div>
            <label for="{idPrefix}-address2" class="mb-1 block text-sm font-medium text-text">
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
            <label for="{idPrefix}-city" class="mb-1 block text-sm font-medium text-text">
              {t("companies.city")}
            </label>
            <input
              id="{idPrefix}-city"
              name="city"
              bind:value={city}
              defaultValue={company.city ?? ""}
              class={inputClass}
            />
          </div>
          <div>
            <label for="{idPrefix}-country" class="mb-1 block text-sm font-medium text-text">
              {t("companies.country")}
            </label>
            <!-- A searchable list of country *names* (#349), not two free-text letters: DE not DU,
               AT not OE, and the org's own default when the record has none. -->
            <CountryInput
              id="{idPrefix}-country"
              name="country"
              bind:value={country}
              fallbackToOrg={!company.id}
            />
          </div>
        </div>
      </fieldset>
    {/if}
    {#if shows("extra")}
      <div>
        <label for="{idPrefix}-status" class="mb-1 block text-sm font-medium text-text">
          {t("companies.field.status")}
        </label>
        <!-- The house type-ahead, never a native select — a closed vocabulary is still a picker
           (#256, and the third bare <select> #348 counted on this screen). -->
        <Combobox
          items={COMPANY_STATUSES.map((option) => ({
            value: option,
            label: t(`companies.status.${option}`),
          }))}
          name="status"
          id="{idPrefix}-status"
          value={status}
          allowEmpty={false}
        />
      </div>
      <div class="sm:col-span-2">
        <span class="mb-1 block text-sm font-medium text-text">
          {t("companies.field.assignees")}
        </span>
        <AssigneePicker
          {members}
          value={company.assignees ?? []}
          id="{idPrefix}-assignees"
          placeholder={t("assignees.add")}
        />
      </div>
    {/if}
  </div>

  {#if shows("extra")}
    {#if definitions.length > 0}
      <CustomFieldsForm
        {definitions}
        values={company.custom ?? {}}
        {locale}
        scope={{ companyId: company.id ?? null }}
      />
    {:else}
      <input type="hidden" name="custom" value={JSON.stringify(company.custom ?? {})} />
    {/if}
  {/if}

  {@render children?.()}

  {#if shows("extra")}
    <p class="text-xs text-text-muted">{t("companies.status_hint")}</p>
  {/if}
</div>
