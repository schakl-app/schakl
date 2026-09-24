<script lang="ts">
  /**
   * Record a one-time product sale **without leaving the record you are looking at**.
   *
   * A product from the price list, sold once to this client (and, when the host is a project,
   * for that project): the pick copies the product's name, description, unit, price and VAT
   * onto the form as visible, changeable defaults — the sale snapshots them, so a re-priced
   * product never rewrites what was sold. A sale need not be on the list at all: type a name.
   *
   * It fetches nothing until it is opened (three small reads; most visits never record a
   * sale), and it does not close on a refusal — the form keeps the typing and prints the reason.
   *
   * **Host contract:** the page spreads `saleActions` (`sales.server.ts`).
   */
  import { untrack } from "svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { getCurrency } from "$lib/core/currency";
  import { can } from "$lib/core/permissions";
  import { InFlight } from "$lib/core/submit.svelte";
  import { orgToday } from "$lib/core/today";
  import Button from "$lib/core/ui/Button.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import { toastSuccess } from "$lib/core/ui/toast.svelte";
  import { projectArchivedLabel, splitProjectOptions } from "$lib/modules/projects/picker";

  import { docMoney, taxRateLabel } from "./types";
  import type { ProductSale, TaxRate } from "./types";

  interface Product {
    id: string;
    name: string;
    code?: string | null;
    description?: string | null;
    unit?: string | null;
    unit_price: string | number;
    tax_rate_id?: string | null;
  }
  interface Project {
    id: string;
    name: string;
    status?: string | null;
    company_id?: string | null;
  }

  let {
    open = $bindable(false),
    companyId,
    projectId = null,
    projectLabel = "",
    editing = null,
    locale,
  }: {
    open?: boolean;
    /** The client the sale is for — the host record's, never a picker here. */
    companyId: string;
    /** Fixed when the host is a project: the sale is for *that* project, shown as a chip. */
    projectId?: string | null;
    projectLabel?: string;
    /** The sale being edited; `null` records a new one. */
    editing?: ProductSale | null;
    locale: string;
  } = $props();

  const busy = new InFlight();
  let products = $state<Product[]>([]);
  let taxRates = $state<TaxRate[]>([]);
  let projects = $state<Project[]>([]);
  let phase = $state<"idle" | "loading" | "ready" | "failed">("idle");
  /** Bumped per open so the form starts clean each time. */
  let session = $state(0);

  async function read<T>(url: string, fallback: T): Promise<T> {
    const response = await fetch(url, { headers: { accept: "application/json" } });
    if (!response.ok) return fallback;
    return (await response.json()) as T;
  }

  async function ensureLookups(): Promise<void> {
    if (phase === "loading" || phase === "ready") return;
    phase = "loading";
    try {
      const [productRows, rateRows, projectPage] = await Promise.all([
        read<Product[]>("/api/v1/invoicing/products", []),
        read<TaxRate[]>("/api/v1/invoicing/tax-rates", []),
        projectId
          ? Promise.resolve({ items: [] as Project[] })
          : read<{ items: Project[] }>(
              `/api/v1/projects?company_id=${encodeURIComponent(companyId)}&limit=200&offset=0&count=false`,
              { items: [] },
            ),
      ]);
      products = productRows;
      taxRates = rateRows.filter((r) => r.active);
      projects = projectPage.items;
      phase = "ready";
    } catch {
      phase = "failed";
    }
  }

  // The form's values: a product pick fills them, and every one stays editable.
  let productPick = $state("");
  let name = $state("");
  let description = $state("");
  let quantity = $state("1");
  let unit = $state("");
  let unitPrice = $state("");
  let taxRateId = $state("");
  let project = $state("");
  let soldOn = $state("");
  let notes = $state("");

  function reset() {
    productPick = editing?.product_id ?? "";
    name = editing?.name ?? "";
    description = editing?.description ?? "";
    quantity = editing ? String(Number(editing.quantity)) : "1";
    unit = editing?.unit ?? "";
    unitPrice = editing ? String(Number(editing.unit_price)) : "";
    taxRateId = editing?.tax_rate_id ?? "";
    project = editing?.project_id ?? projectId ?? "";
    soldOn = editing?.sold_on ?? orgToday();
    notes = editing?.notes ?? "";
  }

  $effect(() => {
    if (!open) return;
    untrack(() => {
      session += 1;
      reset();
      void ensureLookups();
    });
  });

  /** A pick **copies**: the fields stay free text and the sale snapshots what it copied. */
  function pickProduct(id: string) {
    productPick = id;
    const product = products.find((p) => p.id === id);
    if (!product) return;
    name = product.name;
    description = product.description ?? "";
    unit = product.unit ?? "";
    unitPrice = String(Number(product.unit_price));
    taxRateId = product.tax_rate_id ?? "";
  }

  // The picker's "＋ … toevoegen": a product made inline is picked the moment it exists.
  let qcOpen = $state(false);
  let qcName = $state("");
  const qcBusy = new InFlight();
  $effect(() => {
    const created = page.form?.inlineCreated as
      { slot: string; id: string; name?: string } | undefined;
    if (created?.slot === "product" && !products.some((p) => p.id === created.id)) {
      products = [
        ...products,
        { id: created.id, name: created.name ?? "", unit_price: unitPrice || "0", unit },
      ];
      productPick = created.id;
      if (!name) name = created.name ?? "";
    }
  });

  const productItems = $derived(
    products.map((p) => ({
      value: p.id,
      label: p.name,
      hint: [p.code, docMoney(Number(p.unit_price), getCurrency(), locale)]
        .filter(Boolean)
        .join(" · "),
    })),
  );
  const projectPicker = $derived(splitProjectOptions(projects, { selectedId: project, companyId }));
  const canManageProducts = $derived(can(page.data.user, "invoicing.settings.manage"));
  //: Money is frozen once a document bills the sale (the API refuses; the form says so first).
  const invoiced = $derived(editing?.status === "invoiced");

  const inputClass =
    "w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-text outline-none focus:border-brand";
  const labelClass = "mb-1 block text-sm font-medium text-text";
</script>

<Modal bind:open title={editing ? t("invoicing.sales.edit") : t("invoicing.sales.add")}>
  {#if phase === "failed"}
    <p class="text-sm text-red-600 dark:text-red-400">{t("errors.server")}</p>
  {:else if phase !== "ready"}
    <p class="py-6 text-center text-sm text-text-muted">{t("common.loading")}</p>
  {:else}
    {#key session}
      <form
        method="POST"
        action={editing ? "?/updateSale" : "?/createSale"}
        class="space-y-3"
        use:enhance={busy.wrap("sale", () => async ({ result, update }) => {
          if (result.type === "success") {
            open = false;
            toastSuccess(t("invoicing.sales.saved"));
          }
          await update({ reset: false });
        })}
      >
        {#if editing}<input type="hidden" name="id" value={editing.id} />{/if}
        <input type="hidden" name="company_id" value={companyId} />
        <input type="hidden" name="product_id" value={productPick} />

        {#if !editing}
          <div>
            <label class={labelClass} for="sale-product">{t("invoicing.sales.field.product")}</label
            >
            <Combobox
              id="sale-product"
              name="_sale_product"
              items={productItems}
              value={productPick}
              placeholder={t("invoicing.sales.field.product_placeholder")}
              onselect={pickProduct}
              oncreate={canManageProducts
                ? (typed) => {
                    qcName = typed;
                    qcOpen = true;
                  }
                : undefined}
            />
            <p class="mt-1 text-xs text-text-muted">{t("invoicing.sales.field.product_hint")}</p>
          </div>
        {/if}

        <div>
          <label class={labelClass} for="sale-name">{t("common.name_field")}</label>
          <input
            id="sale-name"
            name="name"
            required
            maxlength="255"
            bind:value={name}
            class={inputClass}
            disabled={invoiced}
          />
        </div>
        <div>
          <label class={labelClass} for="sale-description"
            >{t("settings.invoicing.product_description")}</label
          >
          <textarea
            id="sale-description"
            name="description"
            rows="2"
            bind:value={description}
            class={inputClass}
            placeholder={t("settings.invoicing.product_description_hint")}
            disabled={invoiced}></textarea>
        </div>
        <div class="grid gap-3 sm:grid-cols-4">
          <div>
            <label class={labelClass} for="sale-quantity">{t("invoicing.line.quantity")}</label>
            <input
              id="sale-quantity"
              name="quantity"
              type="number"
              step="any"
              min="0.01"
              required
              bind:value={quantity}
              class={inputClass}
              disabled={invoiced}
            />
          </div>
          <div>
            <label class={labelClass} for="sale-unit">{t("invoicing.line.unit")}</label>
            <input
              id="sale-unit"
              name="unit"
              maxlength="20"
              bind:value={unit}
              class={inputClass}
              placeholder="stuk"
              disabled={invoiced}
            />
          </div>
          <div>
            <label class={labelClass} for="sale-price">{t("invoicing.line.unit_price")}</label>
            <input
              id="sale-price"
              name="unit_price"
              type="number"
              step="any"
              min="0"
              required
              bind:value={unitPrice}
              class={inputClass}
              disabled={invoiced}
            />
          </div>
          <div>
            <label class={labelClass} for="sale-tax">{t("invoicing.line.tax")}</label>
            <select
              id="sale-tax"
              name="tax_rate_id"
              bind:value={taxRateId}
              class={inputClass}
              disabled={invoiced}
            >
              <option value="">—</option>
              {#each taxRates as rate (rate.id)}
                <option value={rate.id}>{taxRateLabel(rate, locale)}</option>
              {/each}
            </select>
          </div>
        </div>
        {#if invoiced}
          <p class="text-xs text-text-muted">{t("invoicing.sales.frozen_hint")}</p>
        {/if}
        <div class="grid gap-3 sm:grid-cols-2">
          <div>
            <label class={labelClass} for="sale-project">{t("invoicing.sales.field.project")}</label
            >
            {#if projectId}
              <input type="hidden" name="project_id" value={projectId} />
              <span
                class="inline-flex items-center rounded-full border border-border bg-surface px-2.5 py-1 text-xs text-text"
                >{projectLabel || t("invoicing.sales.field.this_project")}</span
              >
            {:else}
              <Combobox
                id="sale-project"
                name="project_id"
                items={projectPicker.live}
                archived={projectPicker.retired}
                archivedLabel={projectArchivedLabel()}
                bind:value={project}
                placeholder={t("invoicing.sales.field.project_placeholder")}
              />
            {/if}
          </div>
          <div>
            <label class={labelClass} for="sale-sold-on">{t("invoicing.sales.field.sold_on")}</label
            >
            <DateInput id="sale-sold-on" name="sold_on" bind:value={soldOn} required />
          </div>
        </div>
        <div>
          <label class={labelClass} for="sale-notes">{t("invoicing.sales.field.notes")}</label>
          <textarea id="sale-notes" name="notes" rows="2" bind:value={notes} class={inputClass}
          ></textarea>
        </div>

        {#if page.form?.saleError}
          <p class="text-sm text-red-600 dark:text-red-400">{t(page.form.saleError)}</p>
        {/if}
        <div class="flex justify-end gap-2">
          <Button type="button" variant="secondary" onclick={() => (open = false)}>
            {t("common.cancel")}
          </Button>
          <Button type="submit" loading={busy.is("sale")} disabled={busy.active}>
            {t("common.save")}
          </Button>
        </div>
      </form>
    {/key}
  {/if}
</Modal>

<!-- The picker's inline create: the price list's two essentials, the rest in Instellingen. -->
<Modal bind:open={qcOpen} title={t("settings.invoicing.new_product")}>
  <form
    method="POST"
    action="?/createSaleProduct"
    class="space-y-3"
    use:enhance={qcBusy.wrap("qc", () => async ({ result, update }) => {
      if (result.type === "success") qcOpen = false;
      await update({ reset: false });
    })}
  >
    <div>
      <label class={labelClass} for="sale-qc-name">{t("common.name_field")}</label>
      <input
        id="sale-qc-name"
        name="name"
        required
        maxlength="255"
        value={qcName}
        class={inputClass}
      />
    </div>
    <div class="grid gap-3 sm:grid-cols-2">
      <div>
        <label class={labelClass} for="sale-qc-price">{t("invoicing.line.unit_price")}</label>
        <input
          id="sale-qc-price"
          name="unit_price"
          type="number"
          step="any"
          min="0"
          value={unitPrice}
          class={inputClass}
        />
      </div>
      <div>
        <label class={labelClass} for="sale-qc-unit">{t("invoicing.line.unit")}</label>
        <input id="sale-qc-unit" name="unit" maxlength="20" value={unit} class={inputClass} />
      </div>
    </div>
    {#if page.form?.qcError}
      <p class="text-sm text-red-600 dark:text-red-400">{t(page.form.qcError)}</p>
    {/if}
    <div class="flex justify-end gap-2">
      <Button type="button" variant="secondary" onclick={() => (qcOpen = false)}>
        {t("common.cancel")}
      </Button>
      <Button type="submit" loading={qcBusy.is("qc")} disabled={qcBusy.active}>
        {t("common.save")}
      </Button>
    </div>
  </form>
</Modal>
