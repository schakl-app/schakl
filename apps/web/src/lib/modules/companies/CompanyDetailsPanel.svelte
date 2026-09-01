<script lang="ts">
  /**
   * The client's own definition: identity, contact details, billing identity, notes, and the
   * tenant's custom fields.
   *
   * Four things #364 found in this file.
   *
   * It rendered the tenant's **custom fields as raw slugs in a monospace font** (`segment:
   * Industrie`), in Postgres JSONB key order, with dates as `2021-03-01` — while core already
   * had `CustomFieldsView`, whose own docstring says *"used by company panels and entity detail
   * views"*. Contacts, projects and websites use it; the company hub, the page the pattern was
   * written for, did not.
   *
   * It was written in raw `text-neutral-900`/`-700`/`-500`/`-400` throughout, where `app.css`
   * says to use the semantic tokens — so in dark mode every value in the first card on the client
   * page rendered near-black on near-black.
   *
   * It laid every row out `justify-between` across the full card, so a label sat at x=137 and its
   * value at x=1240 with 1100 px of nothing between them. It is a `<dl>` in a grid now, and the
   * panel declares itself `half` width on the API side, so the two-word rows get a lane that fits.
   *
   * And **changing one of these values cost a 512 px dialog that rendered 1445 px tall**, with
   * Opslaan below the fold and the logo uploader on screen because you wanted to fix a postcode.
   * *Tier 2* (docs/UX.md, the shape the contactpersonen panel already uses): each coherent group
   * carries its own ✎ that flips **that group** into edit mode, in place, without moving the
   * page. It is safe to post a partial form because the update action patches only the fields the
   * form actually carried — absent means leave alone, exactly as bulk edit reads it (§18).
   */
  import { Pencil } from "@lucide/svelte";

  import { enhance } from "$app/forms";

  import CustomFieldsView from "$lib/core/customfields/CustomFieldsView.svelte";
  import type { CustomFieldDefinition } from "$lib/core/customfields/types";
  import { t } from "$lib/core/i18n";
  import { formatPhone } from "$lib/core/phone";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import InlineText from "$lib/core/ui/InlineText.svelte";
  import PanelHeader from "$lib/core/ui/PanelHeader.svelte";
  import { toastSuccess } from "$lib/core/ui/toast.svelte";
  import CompanyForm from "$lib/modules/companies/CompanyForm.svelte";

  let {
    companyId,
    data,
    definitions = [],
    locale = "nl",
    title = "",
    onedit,
  }: {
    companyId: string;
    data: Record<string, unknown>;
    /** The tenant's `company` custom-field definitions — labels, order and types (§13). */
    definitions?: CustomFieldDefinition[];
    locale?: string;
    /** The heading the host would have drawn; this panel draws it itself (`ownsHeader`). */
    title?: string;
    /** Tier 3: open the whole record. Absent = the viewer holds no write permission. */
    onedit?: () => void;
  } = $props();

  const clientNumber = $derived(data.client_number as string | null);
  const website = $derived(data.website as string | null);
  const phone = $derived(data.phone as string | null);
  const invoiceEmail = $derived(data.invoice_email as string | null);
  const notes = $derived(data.notes as string | null);
  const custom = $derived((data.custom ?? {}) as Record<string, unknown>);

  /**
   * The name a document is addressed to, and only when it is *news*: `null` on the API means
   * "the label is also the legal name", so drawing it would print the H1 again under a heading
   * that promises something different. A value equal to the label is treated the same way — it
   * arrives from an import or from somebody typing it out of caution, and it is still not a
   * second fact.
   */
  const legalName = $derived.by(() => {
    const value = ((data.legal_name as string | null) ?? "").trim();
    return value && value !== (data.name as string) ? value : null;
  });

  const hasBilling = $derived(
    Boolean(legalName || data.address_line1 || data.city || data.vat_number || data.coc_number),
  );

  /** Which group is open for editing — one at a time, so the card never becomes the dialog. */
  let editing = $state<"identity" | "billing" | null>(null);
  const busy = new InFlight();

  /** What `CompanyForm` needs to render its fields for this record. */
  const values = $derived({
    id: companyId,
    name: data.name as string,
    legal_name: data.legal_name as string | null,
    client_number: clientNumber,
    website,
    phone,
    invoice_email: invoiceEmail,
    notes,
    vat_number: data.vat_number as string | null,
    coc_number: data.coc_number as string | null,
    address_line1: data.address_line1 as string | null,
    house_number: data.house_number as string | null,
    address_line2: data.address_line2 as string | null,
    postal_code: data.postal_code as string | null,
    city: data.city as string | null,
    country: data.country as string | null,
  });
</script>

<PanelHeader {title}>
  {#if onedit}
    <button
      type="button"
      class="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-xs text-text-muted hover:border-brand hover:text-brand"
      onclick={onedit}
    >
      <Pencil size={13} aria-hidden="true" />
      {t("companies.edit_all")}
    </button>
  {/if}
</PanelHeader>

{#snippet editButton(section: "identity" | "billing", label: string)}
  <button
    type="button"
    class="rounded p-1 text-text-muted hover:bg-surface hover:text-brand"
    title={label}
    aria-label={label}
    onclick={() => (editing = section)}
  >
    <Pencil size={13} aria-hidden="true" />
  </button>
{/snippet}

{#snippet formButtons()}
  <div class="flex justify-end gap-2 pt-1">
    <button
      type="button"
      class="rounded-lg border border-border px-3 py-1.5 text-sm"
      onclick={() => (editing = null)}
    >
      {t("common.cancel")}
    </button>
    <Button size="sm" loading={busy.active} disabled={busy.active}>{t("common.save")}</Button>
  </div>
{/snippet}

{#if editing === "identity"}
  <form
    method="POST"
    action="?/update"
    use:enhance={busy.wrap("identity", () => async ({ update, result }) => {
      // `keep`: an edit form has nothing to reset to, and resetting would blank the field the
      // user just saved (docs/UX.md).
      await update({ reset: false });
      if (result.type === "success") {
        editing = null;
        toastSuccess(t("companies.saved"));
      }
    })}
  >
    <CompanyForm
      company={values}
      {locale}
      section="identity"
      idPrefix="details-identity"
      definitions={[]}
    />
    {@render formButtons()}
  </form>
{:else}
  <dl class="grid grid-cols-1 gap-3 sm:grid-cols-2">
    <div class="sm:col-span-2 flex items-start justify-between gap-2">
      <span class="text-xs font-medium uppercase tracking-wide text-text-muted">
        {t("companies.section.identity")}
      </span>
      {#if onedit}{@render editButton("identity", t("companies.section.identity"))}{/if}
    </div>

    <div>
      <dt class="text-xs font-medium uppercase tracking-wide text-text-muted">
        {t("companies.name")}
      </dt>
      <dd class="mt-1 text-sm text-text">{data.name}</dd>
    </div>

    <div>
      <dt class="text-xs font-medium uppercase tracking-wide text-text-muted">
        {t("companies.client_number")}
      </dt>
      <dd class="mt-1 text-sm">
        {#if clientNumber}
          <span class="font-mono tabular-nums text-text">{clientNumber}</span>
        {:else}
          <span class="text-text-muted">—</span>
        {/if}
      </dd>
    </div>

    <div class="min-w-0">
      <dt class="text-xs font-medium uppercase tracking-wide text-text-muted">
        {t("companies.website")}
      </dt>
      <dd class="mt-1 truncate text-sm">
        {#if website}
          <a class="text-brand underline" href={website} target="_blank" rel="noreferrer">
            {website}
          </a>
        {:else}
          <span class="text-text-muted">—</span>
        {/if}
      </dd>
    </div>

    <div class="min-w-0">
      <dt class="text-xs font-medium uppercase tracking-wide text-text-muted">
        {t("companies.phone")}
      </dt>
      <dd class="mt-1 truncate text-sm">
        {#if phone}
          <a class="text-brand underline" href="tel:{phone}">{formatPhone(phone)}</a>
        {:else}
          <span class="text-text-muted">—</span>
        {/if}
      </dd>
    </div>

    <div class="min-w-0 sm:col-span-2">
      <dt class="text-xs font-medium uppercase tracking-wide text-text-muted">
        {t("companies.invoice_email")}
      </dt>
      <dd class="mt-1 truncate text-sm">
        {#if invoiceEmail}
          <a class="text-brand underline" href="mailto:{invoiceEmail}">{invoiceEmail}</a>
        {:else}
          <span class="text-text-muted">—</span>
        {/if}
      </dd>
    </div>

    <!-- Edited in place (#455): notes are the one field on a client that changes between two
         phone calls, and they used to live only inside the slide-over behind every other
         field. Posts `notes` alone to `?/update`; drawn for a reader only when there is text. -->
    {#if notes || onedit}
      <div class="sm:col-span-2">
        <dt class="text-xs font-medium uppercase tracking-wide text-text-muted">
          {t("companies.notes")}
        </dt>
        <dd class="mt-1 text-sm text-text">
          <InlineText
            name="notes"
            value={notes ?? ""}
            placeholder={t("companies.notes_placeholder")}
            canEdit={!!onedit}
            rows={3}
            scope={{ companyId }}
            id="company-notes-inline"
          />
        </dd>
      </div>
    {/if}
  </dl>
{/if}

<div class="mt-4 border-t border-border pt-4">
  {#if editing === "billing"}
    <form
      method="POST"
      action="?/update"
      use:enhance={busy.wrap("billing", () => async ({ update, result }) => {
        // `keep`, as above — this edits what already exists.
        await update({ reset: false });
        if (result.type === "success") {
          editing = null;
          toastSuccess(t("companies.saved"));
        }
      })}
    >
      <CompanyForm
        company={values}
        {locale}
        section="billing"
        idPrefix="details-billing"
        definitions={[]}
      />
      {@render formButtons()}
    </form>
  {:else}
    <div class="flex items-start justify-between gap-2">
      <span class="text-xs font-medium uppercase tracking-wide text-text-muted">
        {t("companies.billing_heading")}
      </span>
      {#if onedit}{@render editButton("billing", t("companies.billing_heading"))}{/if}
    </div>
    <div class="mt-1 text-sm text-text">
      {#if hasBilling}
        <!-- First, because it is what the rest of this block is *for*: an address under a name
             nobody recognises is the failure the split exists to prevent. -->
        {#if legalName}
          <span class="block font-medium">{legalName}</span>
        {/if}
        <!-- Street and house number are separate columns (#241); display recomposes the line,
             so a pre-split record (number still inside the street field) reads unchanged. -->
        {#if data.address_line1}
          <span class="block">
            {[data.address_line1, data.house_number].filter(Boolean).join(" ")}
          </span>
        {/if}
        {#if data.address_line2}<span class="block">{data.address_line2}</span>{/if}
        {#if data.postal_code || data.city}
          <span class="block"
            >{[data.postal_code, data.city, data.country].filter(Boolean).join(" ")}</span
          >
        {/if}
        {#if data.vat_number}
          <span class="block text-text-muted">{t("companies.vat_number")}: {data.vat_number}</span>
        {/if}
        {#if data.coc_number}
          <span class="block text-text-muted">{t("companies.coc_number")}: {data.coc_number}</span>
        {/if}
      {:else}
        <span class="text-text-muted">—</span>
      {/if}
    </div>
  {/if}
</div>

{#if definitions.length > 0 && Object.keys(custom).length > 0}
  <!-- The tenant's own fields, resolved through core: `label_i18n` for the active locale,
       definition order, European dates, arrays and booleans (§13). Edited from the whole-record
       surface: a custom field set is the tenant's own shape and has no fixed size to flip. -->
  <div class="mt-4 border-t border-border pt-4">
    <CustomFieldsView {definitions} values={custom} {locale} />
  </div>
{/if}
