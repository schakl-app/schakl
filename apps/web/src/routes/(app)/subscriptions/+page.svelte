<script lang="ts">
  import { BookmarkPlus, Pencil, Trash2, TrendingUp } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import { fmtMoney, fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { navLabel, pageTitle } from "$lib/core/title";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import I18nTextField from "$lib/core/ui/I18nTextField.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import RichTextEditor from "$lib/core/ui/RichTextEditor.svelte";
  import CustomFieldsForm from "$lib/core/customfields/CustomFieldsForm.svelte";
  import CompanyQuickCreate from "$lib/modules/companies/CompanyQuickCreate.svelte";
  import { SUBSCRIPTION_COLUMNS } from "$lib/modules/subscriptions/columns";
  import PriceIncreaseModal from "$lib/modules/subscriptions/PriceIncreaseModal.svelte";
  import { subscriptionTypeLabel } from "$lib/modules/subscriptions/types";

  let { data, form } = $props();

  type Subscription = (typeof data.subscriptions)[number];
  type Template = (typeof data.templates)[number];

  let showForm = $state(false);
  let editing = $state<Subscription | null>(null);
  let deleteId = $state("");
  let confirmDelete = $state(false);

  // Inline company create from the picker (#115, docs/UX.md — per-picker definition of done).
  let qcCompanyOpen = $state(false);
  let qcCompanyName = $state("");
  let createdCompanyId = $state("");
  // Inline project create from the links picker — same pattern, auto-links the new project.
  let qcProjectOpen = $state(false);
  let qcProjectName = $state("");
  // Inline subscription-type create from the type picker (#142) — same pattern again.
  let qcTypeOpen = $state(false);
  let qcTypeName = $state("");
  let createdTypeId = $state("");
  $effect(() => {
    const created = form?.inlineCreated;
    if (created?.slot === "company") createdCompanyId = created.id;
    if (created?.slot === "subscription_type") createdTypeId = created.id;
    if (created?.slot === "project" && !linkedProjects.some((p) => p.id === created.id)) {
      const name = "name" in created ? created.name : projectName(created.id);
      linkedProjects = [...linkedProjects, { id: created.id, name }];
    }
  });

  const companyItems = $derived(data.companies.map((c) => ({ value: c.id, label: c.name })));
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
  // All filters ride URL params and the API applies them (#153) — the list is paginated.
  function setFilter(key: string, value: string) {
    const url = new URL(page.url);
    if (value) url.searchParams.set(key, value);
    else url.searchParams.delete(key);
    void goto(url, { keepFocus: true, noScroll: true });
  }
  function setTypeFilter(typeId: string) {
    setFilter("type", typeId !== data.typeFilter ? typeId : "");
  }

  // --- the shared DataTable (#153, #24) --------------------------------------
  const table = createTableLayout<Subscription>({
    all: () => SUBSCRIPTION_COLUMNS,
    pref: () => data.table.pref,
    sort: () => data.table.sort,
    cells: () => ({
      name: nameCell,
      company: companyCell,
      type: typeCell,
      amount: amountCell,
      next_invoice: nextInvoiceCell,
      status: statusCell,
      start_date: startCell,
      included_hours: includedCell,
    }),
  });

  // Bulk selection (#153): the bar offers only actions valid for the whole selection (#45).
  let bulkSelected = $state<string[]>([]);
  let bulkDeleteOpen = $state(false);
  let bulkStatusPick = $state("active");

  // "Create from template" (#142): prefill, never a server-side copy — the create form stays
  // the single validation path. Rekeys the form so the defaults re-read.
  let prefill = $state<Template | null>(null);

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
  const projectItems = $derived(
    data.projects
      .filter((p) => !linkedProjects.some((l) => l.id === p.id))
      .map((p) => ({ value: p.id, label: p.name })),
  );
  const linksJson = $derived(
    JSON.stringify(linkedProjects.map((p) => ({ entity_type: "project", entity_id: p.id }))),
  );

  function projectName(id: string): string {
    return data.projects.find((p) => p.id === id)?.name ?? "—";
  }

  function openCreate() {
    editing = null;
    createdCompanyId = "";
    createdTypeId = "";
    prefill = null;
    linkedProjects = [];
    showForm = true;
  }
  function openEdit(sub: Subscription) {
    editing = sub;
    createdCompanyId = "";
    createdTypeId = "";
    prefill = null;
    linkedProjects = (sub.links ?? [])
      .filter((l) => l.entity_type === "project")
      .map((l) => ({ id: l.entity_id, name: projectName(l.entity_id) }));
    showForm = true;
  }

  // Quick-create from a client page (?new=1&company=): the dialog opens with the client set
  // (the same ?company= also filters the list behind it to that client).
  if (page.url.searchParams.has("new")) {
    openCreate();
    createdCompanyId = page.url.searchParams.get("company") ?? "";
  }

  const money = (value: string | number | null | undefined) =>
    value == null ? "—" : fmtMoney(Number(value));

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
      onclick={openCreate}>{t("subscriptions.add")}</button
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

<!-- Filter row (#153) — all server-side: the list is paginated. Client picker, status
     pills, type pills (#142) and the personal column picker share one wrapping row. -->
<div class="mb-4 flex flex-wrap items-center gap-2">
  <div class="w-44">
    <Combobox
      items={companyItems}
      name="_filter_company"
      value={data.companyFilter}
      placeholder={t("subscriptions.filter.company")}
      onselect={(v) => setFilter("company", v)}
      id="filter-company"
    />
  </div>
  {#each STATUSES as status (status)}
    <button
      class="rounded-full px-3 py-1 text-xs font-medium
        {data.statusFilter === status
        ? 'bg-brand/10 text-brand ring-2 ring-brand'
        : 'bg-surface text-text-muted hover:text-text'}"
      aria-pressed={data.statusFilter === status}
      onclick={() => setFilter("status", data.statusFilter === status ? "" : status)}
      >{t(`subscriptions.status.${status}`)}</button
    >
  {/each}
  {#each activeTypes as st (st.id)}
    <button
      class="rounded-full px-3 py-1 text-xs font-medium
        {data.typeFilter === st.id
        ? 'bg-brand/10 text-brand ring-2 ring-brand'
        : 'bg-surface text-text-muted hover:text-text'}"
      aria-pressed={data.typeFilter === st.id}
      onclick={() => setTypeFilter(st.id)}>{subscriptionTypeLabel(st, data.locale)}</button
    >
  {/each}
  {#if data.typeFilter || data.statusFilter || data.companyFilter}
    <button
      class="text-xs text-text-muted underline hover:text-text"
      onclick={() => {
        const url = new URL(page.url);
        url.searchParams.delete("type");
        url.searchParams.delete("status");
        url.searchParams.delete("company");
        void goto(url, { keepFocus: true, noScroll: true });
      }}
    >
      {t("tasks.filter.clear")}
    </button>
  {/if}
  <ColumnPicker
    all={table.pickerColumns}
    visible={table.visibleKeys}
    sort={table.sort}
    onchange={table.onColumnsChange}
    onsort={table.onSort}
  />
</div>

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
  <button
    type="button"
    class="text-left font-medium text-text hover:text-brand"
    onclick={() => openEdit(sub)}>{sub.name}</button
  >
{/snippet}

{#snippet companyCell(sub: Subscription)}
  {#if sub.company_id}
    <a href="/companies/{sub.company_id}" class="text-text-muted hover:text-brand"
      >{sub.company_name}</a
    >
  {:else}<span class="text-text-muted">—</span>{/if}
{/snippet}

{#snippet typeCell(sub: Subscription)}
  <span class="text-text-muted"
    >{sub.subscription_type_id ? typeLabel(sub.subscription_type_id) : "—"}</span
  >
{/snippet}

{#snippet amountCell(sub: Subscription)}
  <span class="tabular-nums text-text">{money(sub.amount)}</span>
  <span class="text-xs text-text-muted">· {t(`subscriptions.interval.${sub.interval}`)}</span>
{/snippet}

{#snippet nextInvoiceCell(sub: Subscription)}
  <span class="tabular-nums text-text-muted"
    >{sub.next_invoice_date ? fmtNumericDate(sub.next_invoice_date) : "—"}</span
  >
{/snippet}

{#snippet statusCell(sub: Subscription)}
  <span class="rounded-md bg-surface px-2 py-0.5 text-xs text-text-muted"
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
  <button type="button" class="min-w-0 flex-1 text-left" onclick={() => openEdit(sub)}>
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

{#snippet bulkBar(ids: string[])}
  <span class="text-xs font-medium text-text">{t("table.selected", { count: ids.length })}</span>
  <form method="POST" action="?/bulkStatus" use:enhance class="flex items-center gap-1.5">
    {#each ids as id (id)}
      <input type="hidden" name="ids" value={id} />
    {/each}
    <select
      name="status"
      bind:value={bulkStatusPick}
      class="rounded-lg border border-border bg-surface-raised px-2 py-1 text-xs"
      aria-label={t("subscriptions.field.status")}
    >
      {#each STATUSES as status (status)}
        <option value={status}>{t(`subscriptions.status.${status}`)}</option>
      {/each}
    </select>
    <button class="rounded-lg bg-brand px-3 py-1.5 text-xs font-medium text-white hover:opacity-90">
      {t("subscriptions.bulk.set_status")}
    </button>
  </form>
  <button
    type="button"
    class="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs text-text hover:border-red-400 hover:text-red-600 dark:hover:border-red-500 dark:hover:text-red-400"
    onclick={() => (bulkDeleteOpen = true)}
  >
    <Trash2 size={13} />
    {t("common.delete")}
  </button>
{/snippet}

<DataTable
  rows={data.subscriptions}
  columns={table.columns}
  sort={table.sort}
  widths={table.widths}
  locale={data.locale}
  actions={rowActions}
  {mobileRow}
  empty={emptyState}
  selectable
  bind:selected={bulkSelected}
  selection={bulkBar}
  onsort={table.onSort}
  onresize={table.onResize}
/>

<ConfirmDialog
  bind:open={bulkDeleteOpen}
  title={t("subscriptions.delete")}
  message={t("subscriptions.bulk.delete_confirm")}
  action="?/bulkDelete"
  fields={{ ids: bulkSelected.join(",") }}
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
          (prefill = data.templates.find((tpl) => tpl.id === e.currentTarget.value) ?? null)}
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
      use:enhance={() =>
        ({ result, update }) => {
          if (result.type === "success") showForm = false;
          void update({ reset: false });
        }}
      class="space-y-4"
    >
      {#if editing}<input type="hidden" name="id" value={editing.id} />{/if}
      <div>
        <label for="sub-name" class="mb-1 block text-sm font-medium text-text"
          >{t("subscriptions.field.name")}</label
        >
        <input
          id="sub-name"
          name="name"
          required
          readonly={!editing && !!prefill}
          value={editing?.name ?? prefill?.name ?? ""}
          class="{inputClass} read-only:bg-surface read-only:text-text-muted"
        />
        {#if !editing && prefill}
          <p class="mt-1 text-xs text-text-muted">{t("subscriptions.name_from_template")}</p>
        {/if}
      </div>
      <div>
        <label for="sub-company" class="mb-1 block text-sm font-medium text-text"
          >{t("subscriptions.field.company")}</label
        >
        <Combobox
          items={companyItems}
          name="company_id"
          value={createdCompanyId || (editing?.company_id ?? "")}
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
          value={createdTypeId ||
            (editing?.subscription_type_id ?? prefill?.subscription_type_id ?? "")}
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
          <select id="sub-interval" name="interval" class={inputClass}>
            {#each INTERVALS as interval (interval)}
              <option
                value={interval}
                selected={(editing?.interval ?? prefill?.interval ?? "monthly") === interval}
                >{t(`subscriptions.interval.${interval}`)}</option
              >
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
            value={editing?.amount ?? prefill?.amount ?? ""}
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
            value={editing?.included_hours ?? prefill?.included_hours ?? ""}
            class={inputClass}
          />
        </div>
        <div>
          <label for="sub-start" class="mb-1 block text-sm font-medium text-text"
            >{t("subscriptions.field.start_date")}</label
          >
          <DateInput name="start_date" id="sub-start" required value={editing?.start_date ?? ""} />
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
          value={editing?.notes ?? prefill?.notes ?? ""}
          scope={{ companyId: (createdCompanyId || editing?.company_id) ?? null }}
        />
      </div>
      {#if data.definitions.length > 0}
        <CustomFieldsForm
          definitions={data.definitions}
          values={editing?.custom ?? {}}
          locale={data.locale}
          scope={{ companyId: (createdCompanyId || editing?.company_id) ?? null }}
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
        <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white"
          >{t("common.save")}</button
        >
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
      use:enhance={() =>
        ({ result, update }) => {
          if (result.type === "success") qcProjectOpen = false;
          void update({ reset: false });
        }}
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
        <Combobox
          items={companyItems}
          name="company_id"
          id="qc-sub-project-company"
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
        <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white"
          >{t("common.create")}</button
        >
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
      use:enhance={() =>
        ({ result, update }) => {
          if (result.type === "success") qcTypeOpen = false;
          void update({ reset: false });
        }}
      class="space-y-3"
    >
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
        <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white"
          >{t("common.create")}</button
        >
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
</form>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("subscriptions.delete")}
  message={t("subscriptions.delete_confirm")}
  action="?/delete"
  fields={{ id: deleteId }}
/>
