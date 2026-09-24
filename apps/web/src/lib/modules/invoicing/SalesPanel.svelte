<script lang="ts">
  /**
   * A client's (or a project's) one-time product sales — what was sold once and whether a
   * document bills it yet.
   *
   * One component for both hosts. The client hub hands it the API panel's payload
   * (`companyId` + `data`, the list route's own shape); the project page hands it the typed
   * entity-panel load (`data` carrying the project's rows and its client) — same rows, same
   * vocabulary, so a field added to `ProductSaleRead` reaches both without a mapping.
   *
   * What is still open leads: the heading figure is the whole set's open amount (never the
   * page's), each open row offers **Factureren** — one draft, one line, the claim made by the
   * ordinary create — and the ＋ records a new sale in place (`SaleDialog`). An invoiced row
   * links to its document. Writes post to the host page's actions (`sales.server.ts`).
   */
  import Pencil from "@lucide/svelte/icons/pencil";
  import Trash2 from "@lucide/svelte/icons/trash-2";
  import FileText from "@lucide/svelte/icons/file-text";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { getCurrency } from "$lib/core/currency";
  import { fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import type { EntityPanelContext } from "$lib/core/registry";
  import { InFlight } from "$lib/core/submit.svelte";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import PanelRows from "$lib/core/ui/PanelRows.svelte";
  import { getLocale } from "$lib/paraglide/runtime";

  import SaleDialog from "./SaleDialog.svelte";
  import { docMoney } from "./types";
  import type { ProductSale } from "./types";

  let {
    companyId = "",
    data,
    context,
    locale = getLocale(),
  }: {
    /** The client hub's host prop; the project page carries the client inside `data`. */
    companyId?: string;
    data: unknown;
    context?: EntityPanelContext;
    locale?: string;
  } = $props();

  const panel = $derived(
    (data ?? {}) as {
      items?: ProductSale[];
      total?: number;
      open_count?: number;
      open_amount?: string | number;
      companyId?: string;
      forbidden?: boolean;
    },
  );
  const sales = $derived(panel.items ?? []);
  const total = $derived(panel.total ?? sales.length);
  const openAmount = $derived(Number(panel.open_amount ?? 0));
  const openCount = $derived(panel.open_count ?? 0);
  const client = $derived(companyId || panel.companyId || context?.companyId || "");
  /** Mounted on a project: the sale is for it, and the dialog shows it as a chip. */
  const projectId = $derived(context && !companyId ? context.entityId : null);
  const currency = $derived(sales[0]?.currency ?? getCurrency());

  // The key the call makes (#310): every write here is `invoice.write`.
  const canWrite = $derived(can(page.data.user, "invoicing.invoice.write"));

  const busy = new InFlight();
  let dialogOpen = $state(false);
  let editing = $state<ProductSale | null>(null);
  let deleteId = $state("");
  let confirmDelete = $state(false);

  function openNew() {
    editing = null;
    dialogOpen = true;
  }
  function openEdit(sale: ProductSale) {
    editing = sale;
    dialogOpen = true;
  }
  function submitInvoice(id: string) {
    (document.getElementById(`invoice-sale-${id}`) as HTMLFormElement | null)?.requestSubmit();
  }
  const money = (value: string | number) => docMoney(Number(value), currency, locale);
</script>

{#if page.form?.saleError && !dialogOpen}
  <p class="mb-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
    {t(page.form.saleError)}
  </p>
{/if}

{#if openCount > 0}
  <p class="mb-2 text-sm text-text">
    <span class="font-semibold tabular-nums">{money(openAmount)}</span>
    <span class="text-text-muted">
      {openCount === 1
        ? t("invoicing.sales.open_one")
        : t("invoicing.sales.open", { count: String(openCount) })}
    </span>
  </p>
{/if}

{#if sales.length === 0}
  <p class="text-sm text-text-muted">{t("invoicing.sales.empty")}</p>
{:else}
  <PanelRows
    rows={sales}
    {total}
    href={client ? `/invoices/uninvoiced?source=sale` : undefined}
    linkLabel={t("invoicing.sales.view_all", { count: total })}
  >
    {#snippet children(shown)}
      <ul class="divide-y divide-border">
        {#each shown as sale (sale.id)}
          <li class="flex items-start justify-between gap-3 py-2">
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <span class="truncate text-sm font-medium text-text">{sale.name}</span>
                {#if sale.status === "invoiced" && sale.invoice_id}
                  <a
                    href="/invoices/{sale.invoice_id}"
                    class="rounded-md bg-surface px-2 py-0.5 text-xs text-brand hover:underline"
                    >{sale.invoice_number
                      ? t("invoicing.billed_periods.on_invoice", { number: sale.invoice_number })
                      : t("invoicing.billed_periods.on_draft")}</a
                  >
                {:else}
                  <span
                    class="rounded-md bg-amber-50 px-2 py-0.5 text-xs text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"
                    >{t("invoicing.sales.status.open")}</span
                  >
                {/if}
              </div>
              <p class="mt-0.5 text-xs text-text-muted tabular-nums">
                {money(sale.amount)}
                {#if Number(sale.quantity) !== 1}
                  · {Number(sale.quantity)} × {money(sale.unit_price)}
                {/if}
                · {fmtNumericDate(sale.sold_on)}
                {#if sale.project_name && !projectId}
                  · <a href="/projects/{sale.project_id}" class="hover:text-brand"
                    >{sale.project_name}</a
                  >
                {/if}
              </p>
            </div>
            {#if canWrite}
              <ActionsMenu
                compact
                items={[
                  ...(sale.status === "open"
                    ? [
                        {
                          label: t("invoicing.sales.invoice_now"),
                          icon: FileText,
                          onclick: () => submitInvoice(sale.id),
                        },
                      ]
                    : []),
                  { label: t("common.edit"), icon: Pencil, onclick: () => openEdit(sale) },
                  ...(sale.status === "open"
                    ? [
                        {
                          label: t("common.delete"),
                          icon: Trash2,
                          danger: true,
                          onclick: () => {
                            deleteId = sale.id;
                            confirmDelete = true;
                          },
                        },
                      ]
                    : []),
                ]}
              />
              <form
                id="invoice-sale-{sale.id}"
                method="POST"
                action="?/invoiceSale"
                class="hidden"
                use:enhance={busy.clear(`invoice-${sale.id}`)}
              >
                <input type="hidden" name="id" value={sale.id} />
              </form>
            {/if}
          </li>
        {/each}
      </ul>
    {/snippet}
  </PanelRows>
{/if}

{#if canWrite && client}
  <div class="mt-3">
    <Button variant="secondary" onclick={openNew}>{t("invoicing.sales.add")}</Button>
  </div>
  <SaleDialog
    bind:open={dialogOpen}
    companyId={client}
    {projectId}
    projectLabel={context?.label ?? ""}
    {editing}
    {locale}
  />
  <ConfirmDialog
    bind:open={confirmDelete}
    title={t("common.delete")}
    message={t("invoicing.sales.delete_confirm")}
    action="?/deleteSale"
    fields={{ id: deleteId }}
  />
{/if}
