<script lang="ts">
  import { BookmarkPlus, Pencil, Trash2, TrendingUp } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import BulkBar from "$lib/core/bulk/BulkBar.svelte";
  import BulkToggle from "$lib/core/bulk/BulkToggle.svelte";
  import BulkResult from "$lib/core/bulk/BulkResult.svelte";
  import type { BulkFieldDef } from "$lib/core/bulk/types";
  import { fmtMoney, fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import FilterBar from "$lib/core/filters/FilterBar.svelte";
  import type { FilterDef } from "$lib/core/filters/types";
  import ImpexBar from "$lib/core/impex/ImpexBar.svelte";
  import { navLabel, pageTitle } from "$lib/core/title";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import Pagination from "$lib/core/ui/Pagination.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import { SUBSCRIPTION_COLUMNS } from "$lib/modules/subscriptions/columns";
  import type { SubscriptionFilterKey } from "$lib/modules/subscriptions/filters";
  import PriceIncreaseModal from "$lib/modules/subscriptions/PriceIncreaseModal.svelte";
  import SubscriptionForm from "$lib/modules/subscriptions/SubscriptionForm.svelte";
  import {
    subscriptionTypeLabel,
    type SubscriptionFormLookups,
  } from "$lib/modules/subscriptions/types";
  import { companyArchivedLabel, splitCompanyOptions } from "$lib/modules/companies/picker";
  import {
    resolveNoteVariables,
    subscriptionNoteValues,
  } from "$lib/modules/subscriptions/variables";

  let { data, form } = $props();

  type Subscription = (typeof data.subscriptions)[number];

  let showForm = $state(false);
  let editing = $state<Subscription | null>(null);
  let deleteId = $state("");
  let confirmDelete = $state(false);

  // What the form's pickers draw from — the section layout's lookups (#290), in the one shape
  // the form takes wherever it is mounted (a client's page gathers the same on demand).
  const formLookups = $derived<SubscriptionFormLookups>({
    companies: data.companies,
    projects: data.projects,
    types: data.types,
    templates: data.templates,
    definitions: data.definitions,
    companyDefinitions: data.companyDefinitions,
    orgAutoInvoiceMode: data.invoicingSettings?.auto_invoice_mode ?? null,
  });
  /** The client a create opens on — from the client page's `?company=`, else none. */
  let createCompanyId = $state("");

  // One split for the client controls on this screen — the list filter and the bulk field —
  // so they can never disagree about which clients are still live. An archived client sits
  // behind the search; whatever is already picked stays on offer (`companies/picker.ts`). The
  // agreement form splits the same list on its own, keyed on the client it holds.
  const companyPicker = $derived(
    splitCompanyOptions(data.companies, { selectedId: data.companyFilter }),
  );
  const companyItems = $derived(companyPicker.live);
  const STATUSES = ["draft", "active", "paused", "cancelled"] as const;

  // Tenant-defined categories (#142): picker items, list labels, and the filter pills.
  const activeTypes = $derived(data.types.filter((st) => st.active));
  const typeItems = $derived(
    activeTypes.map((st) => ({ value: st.id, label: subscriptionTypeLabel(st, data.locale) })),
  );
  function typeLabel(id: string | null | undefined): string {
    return subscriptionTypeLabel(
      data.types.find((st) => st.id === id),
      data.locale,
    );
  }
  /**
   * The list's filters, rendered by the shared bar (#354).
   *
   * This row used to be nine identical plain-text chips — four statuses then five
   * tenant-defined types — with no divider, no label and no heading, so nothing said that
   * pressing *Opgezegd* and pressing *Hosting* narrow along different axes, or whether the two
   * combine. Status stays pills, which is the house treatment for a short, stable vocabulary a
   * reader recognises on sight; the types become a named picker beside the client, because
   * there is no fixed number of them and a tenant with ten would have wrapped the row to three
   * lines. And the list gets the `?q=` box every other list here has.
   */
  const filterDefs: FilterDef<SubscriptionFilterKey>[] = $derived([
    { kind: "search", key: "q", placeholder: t("subscriptions.search_placeholder") },
    {
      kind: "select",
      key: "company",
      placeholder: t("subscriptions.field.company"),
      options: companyItems,
      archived: companyPicker.retired,
      archivedLabel: companyArchivedLabel(),
    },
    {
      kind: "pills",
      key: "status",
      options: STATUSES.map((status) => ({
        value: status,
        label: t(`subscriptions.status.${status}`),
      })),
    },
    {
      kind: "select",
      key: "type",
      placeholder: t("subscriptions.field.type"),
      options: typeItems,
    },
  ]);

  // --- the shared DataTable (#153, #24) --------------------------------------
  const table = createTableLayout<Subscription>({
    all: () => SUBSCRIPTION_COLUMNS,
    pref: () => data.table.pref,
    sort: () => data.table.sort,
    cells: () => ({
      name: nameCell,
      company: companyCell,
      type: typeCell,
      interval: intervalCell,
      amount: amountCell,
      next_invoice: nextInvoiceCell,
      status: statusCell,
      start_date: startCell,
      included_hours: includedCell,
      notes: notesCell,
    }),
  });

  // --- bulk (the ✎ selection mode in the toolbar) --------------------------------------
  // Status, category and client: the three things a whole selection can honestly share — an
  // agreement moved to the client that took the account over, a batch recategorised, a run of
  // drafts activated. The money is deliberately absent. Price, interval and the next invoice
  // date each decide what somebody gets billed and when, and a price change *appends* to the
  // price history rather than replacing it, so a misfired bulk price is permanent; the rate
  // change people actually want is Prijsverhoging (#231), which knows about proration.
  // Mirrors `apps/api/app/modules/subscriptions/bulk.py`, and the labels are the import's, so
  // the two surfaces that name the same column can never name it differently.
  let selecting = $state(false);
  let bulkSelected = $state<string[]>([]);
  const bulkFields: BulkFieldDef[] = $derived([
    {
      key: "status",
      label: t("impex.column.subscription.status"),
      type: "select",
      options: STATUSES.map((status) => ({
        value: status,
        label: t(`subscriptions.status.${status}`),
      })),
    },
    // The tenant's own categories and clients — the same items the form's pickers offer, so a
    // bulk edit can only set what a single edit could.
    { key: "type", label: t("impex.column.subscription.type"), type: "fk", options: typeItems },
    {
      key: "company",
      label: t("impex.column.subscription.company"),
      type: "fk",
      options: companyItems,
    },
  ]);
  // One configuration, spread into the ✎ in the toolbar and the strip above the table: they
  // render in different places and must never disagree about what this list can do.
  const bulkConfig = $derived({
    fields: bulkFields,
    writePermission: "subscriptions.subscription.write",
    deletePermission: "subscriptions.subscription.delete",
    deleteMessage: t("subscriptions.bulk.delete_message", { count: bulkSelected.length }),
    fieldErrors: form?.bulkFields ?? null,
  });

  // Price increase (#231): scope is everything, one type, one subscription or one template.
  // A row's ⋮ shortcut opens the same modal locked to that row.
  let priceOpen = $state(false);
  let priceScope = $state("all");
  let priceLocked = $state(false);
  function openPriceModal(scope = "all", locked = false) {
    priceScope = scope;
    priceLocked = locked;
    priceOpen = true;
  }
  const priceScopeItems = $derived([
    { value: "all", label: t("subscriptions.price_increase.scope_all") },
    ...activeTypes.map((st) => ({
      value: `type:${st.id}`,
      label: subscriptionTypeLabel(st, data.locale),
      hint: t("subscriptions.field.type"),
    })),
    ...data.subscriptions.map((sub) => ({
      value: `subscription:${sub.id}`,
      label: sub.name,
      hint: sub.company_name || undefined,
    })),
    // A template-scoped change needs the catalog grant too (the API enforces it).
    ...(data.canManageTemplates
      ? data.templates.map((tpl) => ({
          value: `template:${tpl.id}`,
          label: tpl.name,
          hint: t("subscriptions.price_increase.scope_template"),
        }))
      : []),
  ]);

  // "Opslaan als standaardabonnement" (UX rule 5): the row posts its own values through a
  // hidden form.
  let tplForm: HTMLFormElement | undefined = $state();
  let tplDraft = $state<Subscription | null>(null);
  function saveAsTemplate(sub: Subscription) {
    tplDraft = sub;
    // Post after the hidden fields re-render with this row's values.
    setTimeout(() => tplForm?.requestSubmit(), 0);
  }

  function openCreate(companyId = "") {
    editing = null;
    createCompanyId = companyId;
    showForm = true;
  }
  function openEdit(sub: Subscription) {
    editing = sub;
    showForm = true;
  }

  // Quick-create from a client page (?new=1&company=): the dialog opens with the client set
  // (the same ?company= also filters the list behind it to that client).
  if (page.url.searchParams.has("new")) {
    openCreate(page.url.searchParams.get("company") ?? "");
  }

  const money = (value: string | number | null | undefined) =>
    value == null ? "—" : fmtMoney(Number(value));

  // Note variables (#259): the note keeps its `{{company_name}}`-style tokens in storage.
  // A saved subscription resolves its own tokens for display — a reader never meets a variable.
  function subNoteDisplay(sub: Subscription): string {
    return resolveNoteVariables(
      sub.notes ?? "",
      subscriptionNoteValues({
        companyName: sub.company_name,
        subscriptionName: sub.name,
        typeLabel: sub.subscription_type_id ? typeLabel(sub.subscription_type_id) : null,
        amount: sub.amount,
        interval: sub.interval,
        includedHours: sub.included_hours,
        startDate: sub.start_date,
        brandName: page.data.theme?.brandName ?? null,
      }),
    );
  }
</script>

<svelte:head>
  <title>{pageTitle(navLabel("subscriptions", t("subscriptions.title")))}</title>
</svelte:head>

<div class="mb-6 flex flex-wrap items-center justify-between gap-3">
  <h1 class="text-xl font-semibold text-text">
    {navLabel("subscriptions", t("subscriptions.title"))}
  </h1>
  <div class="flex flex-wrap items-center gap-2">
    {#if data.canWrite}
      <button
        class="rounded-lg border border-border px-4 py-2 text-sm font-medium text-text hover:border-brand hover:text-brand"
        onclick={() => openPriceModal()}>{t("subscriptions.price_increase.title")}</button
      >
    {/if}
    <button
      class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
      onclick={() => openCreate()}>{t("subscriptions.add")}</button
    >
  </div>
</div>

<!-- Recurring revenue at a glance (#30). Every number opens: the list below is the breakdown. -->
{#if data.summary}
  <div class="mb-6 grid gap-4 sm:grid-cols-3">
    <div class="rounded-xl border border-border bg-surface-raised p-4">
      <p class="text-xs font-semibold uppercase tracking-wide text-text-muted">
        {t("subscriptions.mrr")}
      </p>
      <p class="mt-1 text-2xl font-semibold text-text">{money(data.summary.mrr)}</p>
    </div>
    <div class="rounded-xl border border-border bg-surface-raised p-4">
      <p class="text-xs font-semibold uppercase tracking-wide text-text-muted">
        {t("subscriptions.arr")}
      </p>
      <p class="mt-1 text-2xl font-semibold text-text">{money(data.summary.arr)}</p>
    </div>
    <div class="rounded-xl border border-border bg-surface-raised p-4">
      <p class="text-xs font-semibold uppercase tracking-wide text-text-muted">
        {t("subscriptions.active_count")}
      </p>
      <p class="mt-1 text-2xl font-semibold text-text">{data.summary.active_count}</p>
    </div>
  </div>
{/if}

<FilterBar filters={filterDefs} idPrefix="subscription-filter">
  {#snippet actions()}
    <!-- Export carries what the screen is narrowed by, so the file *is* the list on screen,
         whole (docs/UX.md) — the API declares exactly these on the export route. -->
    <ImpexBar
      entity="subscription"
      readPermission="subscriptions.subscription.read"
      writePermission="subscriptions.subscription.write"
      filters={{
        q: data.filters.q,
        status: data.statusFilter,
        company_id: data.companyFilter,
        subscription_type_id: data.typeFilter,
        sort: data.table.sort,
      }}
      locale={data.locale}
      {form}
    />
    <ColumnPicker
      all={table.pickerColumns}
      visible={table.visibleKeys}
      sort={table.sort}
      onchange={table.onColumnsChange}
      onsort={table.onSort}
    />
    <!-- Last in the toolbar, always: it is the only control here that changes what the *rows*
         do rather than what the list shows, so it sits after Kolommen rather than among the
         list's own controls. Pressing it opens the selection strip above the table. -->
    <BulkToggle bind:selecting bind:selected={bulkSelected} {...bulkConfig} />
  {/snippet}
</FilterBar>

{#if form?.templateSaved}
  <p class="mb-4 rounded-lg border border-border bg-surface-raised px-4 py-2 text-sm text-text">
    {t("subscriptions.template_saved")}
  </p>
{/if}

<PriceIncreaseModal
  bind:open={priceOpen}
  bind:scope={priceScope}
  scopeItems={priceScopeItems}
  locked={priceLocked}
  {form}
/>

{#snippet nameCell(sub: Subscription)}
  {#if data.canWrite}
    <!-- `w-full`, because a button shrinks to fit even as a block box: without it the nowrap
         from `truncate` would let it grow past the column and be cut mid-letter. -->
    <button
      type="button"
      class="block w-full truncate text-left font-medium text-text hover:text-brand"
      onclick={() => openEdit(sub)}>{sub.name}</button
    >
  {:else}
    <!-- A reader — a client on their own agreements — opens the record page; the edit modal
         behind the button above is a form they may not post. -->
    <a
      href={`/subscriptions/${sub.id}`}
      class="block w-full truncate text-left font-medium text-text hover:text-brand">{sub.name}</a
    >
  {/if}
{/snippet}

{#snippet companyCell(sub: Subscription)}
  {#if sub.company_id}
    <a href="/companies/{sub.company_id}" class="block truncate text-text-muted hover:text-brand"
      >{sub.company_name}</a
    >
  {:else}<span class="text-text-muted">—</span>{/if}
{/snippet}

{#snippet typeCell(sub: Subscription)}
  <span class="block truncate text-text-muted"
    >{sub.subscription_type_id ? typeLabel(sub.subscription_type_id) : "—"}</span
  >
{/snippet}

{#snippet intervalCell(sub: Subscription)}
  <span class="block truncate text-text-muted">{t(`subscriptions.interval.${sub.interval}`)}</span>
{/snippet}

<!-- Numbers only (#261): the interval used to ride along inline, and because its label is a
     different length per row it pushed every amount to its own horizontal position. -->
{#snippet amountCell(sub: Subscription)}
  <span class="tabular-nums text-text">{money(sub.amount)}</span>
{/snippet}

{#snippet nextInvoiceCell(sub: Subscription)}
  <span class="tabular-nums text-text-muted"
    >{sub.next_invoice_date ? fmtNumericDate(sub.next_invoice_date) : "—"}</span
  >
{/snippet}

{#snippet statusCell(sub: Subscription)}
  <!-- `inline-block`, so the chip keeps its own width but can still clip: `overflow` does
       nothing to an inline box, and a long label would otherwise be cut mid-letter. -->
  <span
    class="inline-block max-w-full truncate rounded-md bg-surface px-2 py-0.5 align-middle text-xs text-text-muted"
    >{t(`subscriptions.status.${sub.status}`)}</span
  >
{/snippet}

{#snippet startCell(sub: Subscription)}
  <span class="tabular-nums text-text-muted"
    >{sub.start_date ? fmtNumericDate(sub.start_date) : "—"}</span
  >
{/snippet}

{#snippet includedCell(sub: Subscription)}
  <span class="tabular-nums text-text-muted">{sub.included_hours ?? "—"}</span>
{/snippet}

<!-- Notes resolve their variables for reading (#259): a variable is never shown as one. -->
{#snippet notesCell(sub: Subscription)}
  <span class="block max-w-64 truncate text-text-muted">{subNoteDisplay(sub) || "—"}</span>
{/snippet}

{#snippet rowActions(sub: Subscription)}
  <ActionsMenu
    compact
    items={[
      { label: t("common.edit"), icon: Pencil, onclick: () => openEdit(sub) },
      ...(data.canWrite
        ? [
            {
              label: t("subscriptions.price_increase.row_action"),
              icon: TrendingUp,
              onclick: () => openPriceModal(`subscription:${sub.id}`, true),
            },
          ]
        : []),
      ...(data.canManageTemplates
        ? [
            {
              label: t("subscriptions.save_template"),
              icon: BookmarkPlus,
              onclick: () => saveAsTemplate(sub),
            },
          ]
        : []),
      {
        label: t("common.delete"),
        icon: Trash2,
        danger: true,
        onclick: () => {
          deleteId = sub.id;
          confirmDelete = true;
        },
      },
    ]}
  />
{/snippet}

{#snippet mobileRow(sub: Subscription)}
  <!-- `block w-full`, not `flex-1`: the row snippet is rendered into a block wrapper, so the
       flex classes were inert and the button sized to its own text — which is what pushed the
       phone's page 34px wider than the viewport. The lines below can only truncate against a
       definite width. -->
  <button type="button" class="block w-full text-left" onclick={() => openEdit(sub)}>
    <span class="block truncate text-sm font-medium text-text">{sub.name}</span>
    <span class="mt-0.5 block truncate text-xs text-text-muted">
      {sub.company_name} · {money(sub.amount)} ·
      {t(`subscriptions.status.${sub.status}`)}
    </span>
  </button>
{/snippet}

{#snippet emptyState()}
  <p class="p-6 text-sm text-text-muted">{t("subscriptions.empty")}</p>
{/snippet}

<BulkBar {selecting} bind:selected={bulkSelected} {...bulkConfig} />

<BulkResult result={form?.bulkResult} />

<DataTable
  rows={data.subscriptions}
  columns={table.columns}
  sort={table.sort}
  widths={table.widths}
  locale={data.locale}
  actions={rowActions}
  {mobileRow}
  empty={emptyState}
  {selecting}
  bind:selected={bulkSelected}
  onsort={table.onSort}
  onresize={table.onResize}
/>

<Pagination
  total={data.total}
  page={data.paging.page}
  limit={data.paging.limit}
  onsize={table.onPageSize}
/>

<!-- One form for create and edit (use vs edit mode: definition changes live here). The same
     component a client's page mounts in its own dialog (`SubscriptionDialog`); keyed per row so
     its state is seeded once from the row it is about. -->
<Modal bind:open={showForm} title={editing ? t("common.edit") : t("subscriptions.add")}>
  {#key editing?.id ?? "new"}
    <SubscriptionForm
      {editing}
      lookups={formLookups}
      locale={data.locale}
      defaultCompanyId={createCompanyId}
      action={editing ? "?/update" : "?/create"}
      oncancel={() => (showForm = false)}
      onsaved={() => (showForm = false)}
    />
  {/key}
</Modal>

<!-- "Opslaan als standaardabonnement": the row's values, posted through a hidden single-purpose form. -->
<form bind:this={tplForm} method="POST" action="?/saveTemplate" use:enhance class="hidden">
  <input type="hidden" name="name" value={tplDraft?.name ?? ""} />
  <input type="hidden" name="subscription_type_id" value={tplDraft?.subscription_type_id ?? ""} />
  <input type="hidden" name="interval" value={tplDraft?.interval ?? "monthly"} />
  <input type="hidden" name="interval_count" value={tplDraft?.interval_count ?? 1} />
  <input type="hidden" name="amount" value={tplDraft?.amount ?? ""} />
  <input type="hidden" name="included_hours" value={tplDraft?.included_hours ?? ""} />
  <input type="hidden" name="notice_period_days" value={tplDraft?.notice_period_days ?? ""} />
  <input type="hidden" name="notes" value={tplDraft?.notes ?? ""} />
  <!-- This agreement becomes an instance of the preset it just defined, so renaming the
       standard subscription later renames it along with the rest. -->
  <input type="hidden" name="link_subscription_id" value={tplDraft?.id ?? ""} />
</form>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("subscriptions.delete")}
  message={t("subscriptions.delete_confirm")}
  action="?/delete"
  fields={{ id: deleteId }}
/>
