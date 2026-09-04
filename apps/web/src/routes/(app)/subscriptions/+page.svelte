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
  import { InFlight } from "$lib/core/submit.svelte";
  import { navLabel, pageTitle } from "$lib/core/title";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import AutoInvoiceModeField from "$lib/modules/invoicing/AutoInvoiceModeField.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import Pagination from "$lib/core/ui/Pagination.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import I18nLocaleSwitcher from "$lib/core/ui/I18nLocaleSwitcher.svelte";
  import I18nTextField from "$lib/core/ui/I18nTextField.svelte";
  import Markdown from "$lib/core/ui/Markdown.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import RichTextEditor from "$lib/core/ui/RichTextEditor.svelte";
  import CustomFieldsForm from "$lib/core/customfields/CustomFieldsForm.svelte";
  import CompanyQuickCreate from "$lib/modules/companies/CompanyQuickCreate.svelte";
  import { SUBSCRIPTION_COLUMNS } from "$lib/modules/subscriptions/columns";
  import type { SubscriptionFilterKey } from "$lib/modules/subscriptions/filters";
  import PriceIncreaseModal from "$lib/modules/subscriptions/PriceIncreaseModal.svelte";
  import { subscriptionTypeLabel } from "$lib/modules/subscriptions/types";
  import { companyArchivedLabel, splitCompanyOptions } from "$lib/modules/companies/picker";
  import { projectArchivedLabel, splitProjectOptions } from "$lib/modules/projects/picker";
  import {
    hasNoteVariables,
    noteVariableItems,
    notePlaceholder,
    resolveNoteVariables,
    subscriptionNoteValues,
  } from "$lib/modules/subscriptions/variables";

  let { data, form } = $props();

  type Subscription = (typeof data.subscriptions)[number];
  type Template = (typeof data.templates)[number];

  const busy = new InFlight();
  let showForm = $state(false);
  let editing = $state<Subscription | null>(null);
  let deleteId = $state("");
  let confirmDelete = $state(false);

  // Inline company create from the picker (#115, docs/UX.md — per-picker definition of done).
  let qcCompanyOpen = $state(false);
  let qcCompanyName = $state("");
  // Inline project create from the links picker — same pattern, auto-links the new project.
  let qcProjectOpen = $state(false);
  let qcProjectName = $state("");
  // Inline subscription-type create from the type picker (#142) — same pattern again.
  let qcTypeOpen = $state(false);
  let qcTypeName = $state("");

  // The fields a note's variables can draw on (#259), mirrored as reactive state so the edit
  // preview resolves live as you type. The picked company/type live here too, so an inline-created
  // one auto-selects and the nested project quick-create inherits the chosen client (#247).
  // (Re)seeded whenever the dialog opens on a row.
  let pv = $state({
    name: "",
    companyId: "",
    typeId: "",
    amount: "",
    interval: "monthly",
    includedHours: "",
    startDate: "",
    notes: "",
  });
  function seedPreview() {
    pv = {
      name: editing?.name ?? "",
      companyId: editing?.company_id ?? "",
      typeId: editing?.subscription_type_id ?? "",
      amount: String(editing?.amount ?? ""),
      interval: editing?.interval ?? "monthly",
      includedHours: String(editing?.included_hours ?? ""),
      startDate: editing?.start_date ?? "",
      notes: editing?.notes ?? "",
    };
  }
  /**
   * Picking a preset fills in what the preset *defines* and leaves the rest of the form alone.
   * Re-seeding wholesale wiped the client and the start date — which the preset has no opinion
   * about — so a dialog opened from a client page (`?company=`) or an inline-created client
   * (#247) lost it the moment a standard subscription was chosen, and the note's
   * `{{company_name}}` fell back to the `[Bedrijfsnaam]` placeholder.
   */
  function applyTemplate(tpl: Template | null) {
    prefill = tpl;
    if (!tpl) return;
    pv.name = tpl.name;
    pv.typeId = tpl.subscription_type_id ?? "";
    pv.amount = String(tpl.amount ?? "");
    pv.interval = tpl.interval ?? "monthly";
    pv.includedHours = String(tpl.included_hours ?? "");
    pv.notes = tpl.notes ?? "";
  }
  $effect(() => {
    const created = form?.inlineCreated;
    if (created?.slot === "company") pv.companyId = created.id;
    if (created?.slot === "subscription_type") pv.typeId = created.id;
    if (created?.slot === "project" && !linkedProjects.some((p) => p.id === created.id)) {
      const name = "name" in created ? created.name : projectName(created.id);
      linkedProjects = [...linkedProjects, { id: created.id, name }];
    }
  });

  // One split for all three client controls on this screen — the list filter, the agreement
  // form and the inline project quick-create — so they can never disagree about which clients
  // are still live. An archived client sits behind the search; whatever is already picked in
  // any of them stays on offer (`companies/picker.ts`).
  const companyPicker = $derived(
    splitCompanyOptions(data.companies, { selectedId: [data.companyFilter, pv.companyId] }),
  );
  const companyItems = $derived(companyPicker.live);
  const STATUSES = ["draft", "active", "paused", "cancelled"] as const;
  const INTERVALS = ["monthly", "quarterly", "yearly"] as const;

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

  // "Create from template" (#142): prefill, never a server-side copy — the create form stays
  // the single validation path. Rekeys the form so the defaults re-read.
  let prefill = $state<Template | null>(null);

  // The preset an agreement came from, while it still carries that preset's name: renaming the
  // preset renames this row too, and giving it its own name here is how it stops following.
  const followedTemplate = $derived(
    editing?.subscription_template_id
      ? (data.templates.find(
          (tpl) => tpl.id === editing?.subscription_template_id && tpl.name === editing?.name,
        ) ?? null)
      : null,
  );

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

  // Projects linked to the agreement being edited: time on these counts toward the bundle.
  let linkedProjects = $state<{ id: string; name: string }[]>([]);
  // A finished project is not something to *add* to a running agreement, so it drops behind the
  // search and says which status it is in; already-linked ones drop out entirely, as before.
  const projectPicker = $derived(
    splitProjectOptions(
      data.projects.filter((p) => !linkedProjects.some((l) => l.id === p.id)),
      { selectedId: linkedProjects.map((l) => l.id) },
    ),
  );
  const projectItems = $derived(projectPicker.live);
  const linksJson = $derived(
    JSON.stringify(linkedProjects.map((p) => ({ entity_type: "project", entity_id: p.id }))),
  );

  function projectName(id: string): string {
    return data.projects.find((p) => p.id === id)?.name ?? "—";
  }

  function openCreate(companyId = "") {
    editing = null;
    prefill = null;
    linkedProjects = [];
    seedPreview();
    if (companyId) pv.companyId = companyId;
    showForm = true;
  }
  function openEdit(sub: Subscription) {
    editing = sub;
    prefill = null;
    linkedProjects = (sub.links ?? [])
      .filter((l) => l.entity_type === "project")
      .map((l) => ({ id: l.entity_id, name: projectName(l.entity_id) }));
    seedPreview();
    showForm = true;
  }

  // Quick-create from a client page (?new=1&company=): the dialog opens with the client set
  // (the same ?company= also filters the list behind it to that client).
  if (page.url.searchParams.has("new")) {
    openCreate(page.url.searchParams.get("company") ?? "");
  }

  const money = (value: string | number | null | undefined) =>
    value == null ? "—" : fmtMoney(Number(value));

  // Note variables (#259): the note keeps its `{{company_name}}`-style tokens in storage. They
  // are resolved only for reading — a live preview while editing (an unknown value shown as a
  // `[label]` placeholder), and the finished text wherever the note is displayed.
  const variableItems = $derived(noteVariableItems(t));
  const previewNotes = $derived(
    hasNoteVariables(pv.notes)
      ? resolveNoteVariables(
          pv.notes,
          subscriptionNoteValues({
            companyName: data.companies.find((c) => c.id === pv.companyId)?.name ?? null,
            subscriptionName: pv.name,
            typeLabel: pv.typeId ? typeLabel(pv.typeId) : null,
            amount: pv.amount,
            interval: pv.interval,
            includedHours: pv.includedHours,
            startDate: pv.startDate,
            brandName: page.data.theme?.brandName ?? null,
          }),
          { placeholder: notePlaceholder(t) },
        )
      : "",
  );
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

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
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

<!-- One form for create and edit (use vs edit mode: definition changes live here). -->
<Modal bind:open={showForm} title={editing ? t("common.edit") : t("subscriptions.add")}>
  <!-- Prefill from a preset (#142). Outside the {#key} so picking one survives the rekey. -->
  {#if !editing && data.templates.length > 0}
    <div class="mb-4">
      <label for="sub-template" class="mb-1 block text-sm font-medium text-text"
        >{t("subscriptions.from_template")}</label
      >
      <select
        id="sub-template"
        class={inputClass}
        value={prefill?.id ?? ""}
        onchange={(e) =>
          applyTemplate(data.templates.find((tpl) => tpl.id === e.currentTarget.value) ?? null)}
      >
        <option value="">—</option>
        {#each data.templates as tpl (tpl.id)}
          <option value={tpl.id}>{tpl.name}</option>
        {/each}
      </select>
    </div>
  {/if}
  {#key `${editing?.id ?? "new"}-${prefill?.id ?? ""}`}
    <form
      method="POST"
      action={editing ? "?/update" : "?/create"}
      use:enhance={busy.wrap("save", () => ({ result, update }) => {
        if (result.type === "success") showForm = false;
        void update({ reset: false });
      })}
      class="space-y-4"
    >
      {#if editing}<input type="hidden" name="id" value={editing.id} />{/if}
      <!-- Which preset this came from: provenance, so a later rename of the standard
           subscription reaches this agreement's (read-only, preset-owned) name. -->
      {#if !editing && prefill}
        <input type="hidden" name="subscription_template_id" value={prefill.id} />
      {/if}
      <div>
        <label for="sub-name" class="mb-1 block text-sm font-medium text-text"
          >{t("subscriptions.field.name")}</label
        >
        <input
          id="sub-name"
          name="name"
          required
          readonly={!editing && !!prefill}
          bind:value={pv.name}
          class="{inputClass} read-only:bg-surface read-only:text-text-muted"
        />
        {#if !editing && prefill}
          <p class="mt-1 text-xs text-text-muted">{t("subscriptions.name_from_template")}</p>
        {:else if followedTemplate}
          <p class="mt-1 text-xs text-text-muted">
            {t("subscriptions.follows_template", { name: followedTemplate.name })}
          </p>
        {/if}
      </div>
      <div>
        <label for="sub-company" class="mb-1 block text-sm font-medium text-text"
          >{t("subscriptions.field.company")}</label
        >
        <Combobox
          items={companyItems}
          archived={companyPicker.retired}
          archivedLabel={companyArchivedLabel()}
          name="company_id"
          bind:value={pv.companyId}
          id="sub-company"
          placeholder={t("subscriptions.field.company")}
          oncreate={(name) => {
            qcCompanyName = name;
            qcCompanyOpen = true;
          }}
        />
      </div>
      <div>
        <label for="sub-type" class="mb-1 block text-sm font-medium text-text"
          >{t("subscriptions.field.type")}</label
        >
        <Combobox
          items={typeItems}
          name="subscription_type_id"
          bind:value={pv.typeId}
          id="sub-type"
          placeholder={t("subscriptions.field.type")}
          oncreate={data.canManageTypes
            ? (name) => {
                qcTypeName = name;
                qcTypeOpen = true;
              }
            : undefined}
        />
      </div>
      <div class="grid gap-3 sm:grid-cols-2">
        <div>
          <label for="sub-status" class="mb-1 block text-sm font-medium text-text"
            >{t("subscriptions.field.status")}</label
          >
          <select id="sub-status" name="status" class={inputClass}>
            {#each STATUSES as status (status)}
              <option value={status} selected={(editing?.status ?? "active") === status}
                >{t(`subscriptions.status.${status}`)}</option
              >
            {/each}
          </select>
        </div>
        <div>
          <label for="sub-interval" class="mb-1 block text-sm font-medium text-text"
            >{t("subscriptions.field.interval")}</label
          >
          <select id="sub-interval" name="interval" class={inputClass} bind:value={pv.interval}>
            {#each INTERVALS as interval (interval)}
              <option value={interval}>{t(`subscriptions.interval.${interval}`)}</option>
            {/each}
          </select>
        </div>
        <div>
          <label for="sub-amount" class="mb-1 block text-sm font-medium text-text"
            >{t("subscriptions.field.amount")}</label
          >
          <input
            id="sub-amount"
            name="amount"
            type="number"
            min="0"
            step="0.01"
            required={!editing}
            value={pv.amount}
            oninput={(e) => (pv.amount = e.currentTarget.value)}
            class={inputClass}
          />
        </div>
        <div>
          <label for="sub-included" class="mb-1 block text-sm font-medium text-text"
            >{t("subscriptions.field.included_hours")}</label
          >
          <input
            id="sub-included"
            name="included_hours"
            type="number"
            min="0"
            step="0.5"
            value={pv.includedHours}
            oninput={(e) => (pv.includedHours = e.currentTarget.value)}
            class={inputClass}
          />
        </div>
        <div>
          <label for="sub-start" class="mb-1 block text-sm font-medium text-text"
            >{t("subscriptions.field.start_date")}</label
          >
          <DateInput name="start_date" id="sub-start" required bind:value={pv.startDate} />
        </div>
        <!-- Edit only (#223): on create there is nothing to anchor a "next invoice" against —
             the API derives the first cycle boundary (start + one period) on activation. -->
        {#if editing}
          <div>
            <label for="sub-next" class="mb-1 block text-sm font-medium text-text"
              >{t("subscriptions.field.next_invoice")}</label
            >
            <DateInput
              name="next_invoice_date"
              id="sub-next"
              value={editing?.next_invoice_date ?? ""}
            />
          </div>
        {/if}
      </div>
      <!-- How far the cycle cron takes this agreement's invoice. Asked here rather than
           inferred, because an agency automating twelve hosting retainers still assembles by
           hand the one client whose invoice is argued over every month, and per-org config
           cannot express that. "Follow the organisation setting" is the default. -->
      <AutoInvoiceModeField
        name="auto_invoice_mode"
        value={editing?.auto_invoice_mode ?? ""}
        inheritable
        orgMode={data.invoicingSettings?.auto_invoice_mode ?? "draft"}
      />
      <div>
        <span class="mb-1 block text-sm font-medium text-text"
          >{t("subscriptions.field.projects")}</span
        >
        {#if linkedProjects.length > 0}
          <div class="mb-2 flex flex-wrap gap-1.5">
            {#each linkedProjects as proj (proj.id)}
              <span
                class="inline-flex items-center gap-1 rounded-full border border-border bg-surface px-2.5 py-0.5 text-xs text-text"
              >
                {proj.name}
                <button
                  type="button"
                  class="text-text-muted hover:text-red-600 dark:hover:text-red-400"
                  aria-label={t("common.delete")}
                  onclick={() => (linkedProjects = linkedProjects.filter((p) => p.id !== proj.id))}
                  >✕</button
                >
              </span>
            {/each}
          </div>
        {/if}
        <Combobox
          items={projectItems}
          archived={projectPicker.retired}
          archivedLabel={projectArchivedLabel()}
          name="link_project_picker"
          id="sub-projects"
          placeholder={t("subscriptions.field.projects")}
          onselect={(value) => {
            if (value && !linkedProjects.some((p) => p.id === value)) {
              linkedProjects = [...linkedProjects, { id: value, name: projectName(value) }];
            }
          }}
          oncreate={(name) => {
            qcProjectName = name;
            qcProjectOpen = true;
          }}
        />
        <input type="hidden" name="links" value={linksJson} />
        <p class="mt-1 text-xs text-text-muted">{t("subscriptions.field.projects_help")}</p>
      </div>
      <div>
        <label for="sub-notes" class="mb-1 block text-sm font-medium text-text"
          >{t("subscriptions.field.notes")}</label
        >
        <RichTextEditor
          id="sub-notes"
          name="notes"
          rows={2}
          value={pv.notes}
          variables={variableItems}
          scope={{ companyId: pv.companyId || null }}
          onchange={(v) => (pv.notes = v)}
        />
        <p class="mt-1 text-xs text-text-muted">{t("subscriptions.variables.hint")}</p>
        {#if hasNoteVariables(pv.notes)}
          <div class="mt-2 rounded-lg border border-border bg-surface p-3">
            <p class="mb-1 text-xs font-medium text-text-muted">
              {t("subscriptions.variables.preview")}
            </p>
            <Markdown value={previewNotes} />
          </div>
        {/if}
      </div>
      {#if data.definitions.length > 0}
        <CustomFieldsForm
          definitions={data.definitions}
          values={editing?.custom ?? {}}
          locale={data.locale}
          scope={{ companyId: pv.companyId || null }}
        />
      {:else}
        <input type="hidden" name="custom" value={JSON.stringify(editing?.custom ?? {})} />
      {/if}
      {#if form?.error}
        <p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
      {/if}
      <div class="flex justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm text-text"
          onclick={() => (showForm = false)}>{t("common.cancel")}</button
        >
        <Button loading={busy.is("save")} disabled={busy.active}>{t("common.save")}</Button>
      </div>
    </form>
  {/key}
</Modal>

<CompanyQuickCreate
  bind:open={qcCompanyOpen}
  name={qcCompanyName}
  definitions={data.companyDefinitions}
  locale={data.locale}
  error={form?.qcError ?? null}
/>

<!-- Inline project create from the links picker (docs/UX.md — per-picker definition of done). -->
<Modal bind:open={qcProjectOpen} title={t("time.quick_create.project")}>
  {#key qcProjectName + String(qcProjectOpen)}
    <form
      method="POST"
      action="?/createProject"
      use:enhance={busy.wrap("qcProject", () => ({ result, update }) => {
        if (result.type === "success") qcProjectOpen = false;
        void update({ reset: false });
      })}
      class="space-y-3"
    >
      <div>
        <label for="qc-sub-project-name" class="mb-1 block text-sm font-medium text-text"
          >{t("projects.field.name")}</label
        >
        <input
          id="qc-sub-project-name"
          name="name"
          value={qcProjectName}
          required
          class={inputClass}
        />
      </div>
      <div>
        <label for="qc-sub-project-company" class="mb-1 block text-sm font-medium text-text"
          >{t("projects.field.company")}</label
        >
        <!-- Required: a project belongs to a client. The agreement's client is the default. -->
        <Combobox
          items={companyItems}
          archived={companyPicker.retired}
          archivedLabel={companyArchivedLabel()}
          name="company_id"
          value={pv.companyId}
          id="qc-sub-project-company"
          allowEmpty={false}
          placeholder={t("projects.field.company")}
        />
      </div>
      {#if form?.qcError}
        <p class="text-sm text-red-600 dark:text-red-400">{t(form.qcError)}</p>
      {/if}
      <div class="flex justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm text-text"
          onclick={() => (qcProjectOpen = false)}>{t("common.cancel")}</button
        >
        <Button loading={busy.is("qcProject")} disabled={busy.active}>{t("common.create")}</Button>
      </div>
    </form>
  {/key}
</Modal>

<!-- Inline subscription-type create from the picker (#142, docs/UX.md — per-picker rule).
     The full type dialog; the spawn list stays in Instellingen → Abonnementen. -->
<Modal bind:open={qcTypeOpen} title={t("settings.subscriptions.new_type")}>
  {#key qcTypeName + String(qcTypeOpen)}
    <form
      method="POST"
      action="?/createType"
      use:enhance={busy.wrap("qcType", () => ({ result, update }) => {
        if (result.type === "success") qcTypeOpen = false;
        void update({ reset: false });
      })}
      class="space-y-3"
    >
      <I18nLocaleSwitcher />
      {#key qcTypeName}
        <I18nTextField
          label={t("common.label_field")}
          basename="label"
          values={{ nl: qcTypeName }}
          idPrefix="qc-type"
        />
      {/key}
      {#if form?.qcError}
        <p class="text-sm text-red-600 dark:text-red-400">{t(form.qcError)}</p>
      {/if}
      <div class="flex justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm text-text"
          onclick={() => (qcTypeOpen = false)}>{t("common.cancel")}</button
        >
        <Button loading={busy.is("qcType")} disabled={busy.active}>{t("common.create")}</Button>
      </div>
    </form>
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
