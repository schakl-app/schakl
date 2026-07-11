<script lang="ts">
  import { Ban, Pencil, Plus, Repeat } from "@lucide/svelte";

  import { page } from "$app/state";
  import { fmtDayMonth } from "$lib/core/format";
  import { can } from "$lib/core/permissions";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import { labelDotClass } from "$lib/core/ui/colors";
  import { LEAVE_COLUMNS } from "$lib/modules/leave/columns";
  import LeaveRequestForm from "$lib/modules/leave/LeaveRequestForm.svelte";
  import LeaveStatusPill from "$lib/modules/leave/LeaveStatusPill.svelte";
  import RecurringDaysManager from "$lib/modules/leave/RecurringDaysManager.svelte";
  import { fmtHours, hoursToDays, typeLabel, type LeaveTypeInfo } from "$lib/modules/leave/format";

  let { data, form } = $props();

  type Request = (typeof data.requests)[number];

  const table = createTableLayout<Request>({
    all: () => LEAVE_COLUMNS,
    pref: () => data.table.pref,
    sort: () => data.table.sort,
    cells: () => ({
      period: periodCell,
      type: typeCell,
      hours: hoursCell,
      days: daysCell,
      status: statusCell,
    }),
  });

  const types = $derived(data.leaveTypes as LeaveTypeInfo[]);
  const typeById = $derived(Object.fromEntries(types.map((lt) => [lt.id, lt])));
  const remainingByType = $derived(
    Object.fromEntries(data.balances.map((b) => [b.leave_type_id, Number(b.remaining_hours)])),
  );

  let createOpen = $state(false);
  // Recurring free days, self-service (#107): only for auto-approve types — generated days
  // are pre-approved, so approval-requiring types stay a manager's act (the API enforces it).
  const selfServiceTypes = $derived(types.filter((lt) => lt.active && !lt.requires_approval));
  let recurringOpen = $state(false);
  // Deep link from a calendar chip (#106): `?request=<id>` opens that request's edit modal on
  // arrival. Resolved once, into state initializers, not a derived — the surface opens on
  // load and the user can then close it (the same pattern as core/edit-intent.ts).
  function deepLinkedRequest(): Request | null {
    const id = page.url.searchParams.get("request");
    if (!id) return null;
    return data.requests.find((r: Request) => r.id === id && canEdit(r)) ?? null;
  }
  const initialEdit = deepLinkedRequest();
  let editRequest = $state<Request | null>(initialEdit);
  let editOpen = $state(initialEdit !== null);
  let cancelId = $state("");
  let cancelOpen = $state(false);

  function openEdit(request: (typeof data.requests)[number]) {
    editRequest = request;
    editOpen = true;
  }

  // #72: editing and cancelling are no longer pending-only. Approved leave is editable — the API
  // decides whether the save returns it to pending. Cancel is offered on an approved request only
  // when it would not need approval to undo (an approver, or the owner's own future self-service
  // leave); otherwise the API would 403 and offering it is a dead end.
  const canApprove = $derived(can(page.data.user, "leave.request.approve"));
  const todayIso = new Date().toISOString().slice(0, 10);

  function canEdit(request: Request): boolean {
    return request.status === "pending" || request.status === "approved";
  }

  function canCancel(request: Request): boolean {
    if (request.status === "pending") return true;
    if (request.status !== "approved") return false;
    if (canApprove) return true;
    const type = typeById[request.leave_type_id];
    const selfServable = type ? !type.requires_approval : false;
    return selfServable && request.start_date >= todayIso;
  }

  function period(request: { start_date: string; end_date: string }): string {
    return request.start_date === request.end_date
      ? fmtDayMonth(request.start_date)
      : `${fmtDayMonth(request.start_date)} – ${fmtDayMonth(request.end_date)}`;
  }

  const yearLink = (year: number) => `?year=${year}`;
</script>

<svelte:head>
  <title>{pageTitle(t("leave.title"))}</title>
</svelte:head>

<div class="mb-4 flex flex-wrap items-center justify-between gap-3">
  <div class="flex items-center gap-3">
    <h1 class="text-xl font-semibold text-text">{t("leave.title")}</h1>
    <div class="flex items-center gap-1 text-sm" data-sveltekit-preload-data="hover">
      <a
        href={yearLink(data.year - 1)}
        class="rounded px-1.5 py-0.5 text-text-muted hover:text-brand">‹</a
      >
      <span class="font-medium text-text">{data.year}</span>
      <a
        href={yearLink(data.year + 1)}
        class="rounded px-1.5 py-0.5 text-text-muted hover:text-brand">›</a
      >
    </div>
  </div>
  <div class="flex flex-wrap items-center gap-2">
    {#if selfServiceTypes.length > 0}
      <button
        type="button"
        class="flex items-center gap-2 rounded-lg border border-border px-4 py-2 text-sm font-medium text-text hover:border-brand hover:text-brand"
        onclick={() => (recurringOpen = true)}
      >
        <Repeat size={16} />
        {t("leave.recurring.title")}
      </button>
    {/if}
    <button
      type="button"
      class="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
      onclick={() => (createOpen = true)}
    >
      <Plus size={16} />
      {t("leave.request_button")}
    </button>
  </div>
</div>

<!-- Balances per balance-tracked type -->
<div class="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
  {#each data.balances as balance (balance.leave_type_id)}
    {@const leaveType = typeById[balance.leave_type_id]}
    <div class="rounded-xl border border-border bg-surface-raised p-5">
      <div class="mb-2 flex items-center gap-2">
        <span class="h-2.5 w-2.5 rounded-full {labelDotClass(leaveType?.color ?? '')}"></span>
        <h2 class="text-sm font-semibold text-text">
          {typeLabel(leaveType, data.locale)}
        </h2>
      </div>
      <p
        class="text-2xl font-semibold {Number(balance.remaining_hours) < 0
          ? 'text-red-600 dark:text-red-400'
          : 'text-text'}"
      >
        {t("leave.balance.remaining", { hours: fmtHours(balance.remaining_hours) })}
      </p>
      <p class="mt-1 text-sm text-text-muted">
        {t("leave.balance.days_equiv", {
          days: fmtHours(hoursToDays(balance.remaining_hours, data.hoursPerDay)),
        })}
        · {t("leave.balance.of_total", { hours: fmtHours(balance.entitled_hours) })}
      </p>
      {#if Number(balance.pending_hours) > 0}
        <p class="mt-1 text-xs text-amber-600 dark:text-amber-400">
          {t("leave.balance.pending", { hours: fmtHours(balance.pending_hours) })}
        </p>
      {/if}
    </div>
  {:else}
    <p
      class="rounded-xl border border-border bg-surface-raised p-5 text-sm text-text-muted sm:col-span-2 lg:col-span-3"
    >
      {t("leave.balance.none")}
    </p>
  {/each}
</div>

{#snippet periodCell(request: Request)}
  <span class="font-medium text-text">
    {period(request)}
    {#if request.note}
      <span class="mt-0.5 block max-w-[16rem] truncate text-xs font-normal text-text-muted"
        >{request.note}</span
      >
    {/if}
  </span>
{/snippet}

{#snippet typeCell(request: Request)}
  {@const leaveType = typeById[request.leave_type_id]}
  <span class="inline-flex items-center gap-1.5 text-text">
    <span class="h-2 w-2 rounded-full {labelDotClass(leaveType?.color ?? '')}"></span>
    {typeLabel(leaveType, data.locale)}
  </span>
{/snippet}

{#snippet hoursCell(request: Request)}
  <span class="text-text">{fmtHours(request.hours)}</span>
{/snippet}

{#snippet daysCell(request: Request)}
  <!-- Verlof is tracked in hours and shown with a days equivalent (docs/UX.md). -->
  <span class="text-text-muted">≈ {fmtHours(hoursToDays(request.hours, data.hoursPerDay))}</span>
{/snippet}

{#snippet statusCell(request: Request)}
  <LeaveStatusPill status={request.status} />
  {#if request.status === "rejected" && request.decision_note}
    <span class="mt-0.5 block max-w-[14rem] truncate text-xs text-text-muted"
      >{request.decision_note}</span
    >
  {/if}
{/snippet}

{#snippet rowActions(request: Request)}
  {@const items = [
    ...(canEdit(request)
      ? [{ label: t("common.edit"), icon: Pencil, onclick: () => openEdit(request) }]
      : []),
    ...(canCancel(request)
      ? [
          {
            label: t("leave.requests.cancel"),
            icon: Ban,
            danger: true,
            onclick: () => {
              cancelId = request.id;
              cancelOpen = true;
            },
          },
        ]
      : []),
  ]}
  {#if items.length > 0}
    <ActionsMenu compact {items} />
  {/if}
{/snippet}

{#snippet mobileRow(request: Request)}
  <div class="flex items-center gap-3">
    <span class="min-w-0 flex-1">
      <span class="font-medium text-text">{period(request)}</span>
      <span class="mt-0.5 block text-sm text-text-muted">
        {typeLabel(typeById[request.leave_type_id], data.locale)} · {fmtHours(request.hours)}
      </span>
    </span>
    <LeaveStatusPill status={request.status} />
    {@render rowActions(request)}
  </div>
{/snippet}

{#snippet emptyState()}
  <p class="rounded-xl border border-border bg-surface-raised p-6 text-sm text-text-muted">
    {t("leave.requests.empty")}
  </p>
{/snippet}

<!-- My requests -->
<div class="mb-2 flex items-center justify-between">
  <h2 class="text-xs font-semibold uppercase tracking-wide text-text-muted">
    {t("leave.requests.heading")}
  </h2>
  <ColumnPicker
    all={table.pickerColumns}
    visible={table.visibleKeys}
    sort={table.sort}
    onchange={table.onColumnsChange}
    onsort={table.onSort}
  />
</div>

<DataTable
  rows={data.requests}
  columns={table.columns}
  sort={table.sort}
  widths={table.widths}
  locale={data.locale}
  actions={rowActions}
  {mobileRow}
  empty={emptyState}
  onsort={table.onSort}
  onresize={table.onResize}
/>

<Modal bind:open={createOpen} title={t("leave.request_button")}>
  <LeaveRequestForm
    types={types.filter((lt) => lt.active)}
    balances={remainingByType}
    canBackdate={can(page.data.user, "leave.request.write", "any")}
    error={form?.error ?? null}
    ondone={() => (createOpen = false)}
  />
</Modal>

<Modal bind:open={editOpen} title={t("leave.requests.edit")}>
  {#if editRequest}
    {#key editRequest.id}
      <LeaveRequestForm
        types={types.filter((lt) => lt.active)}
        balances={remainingByType}
        request={editRequest}
        canBackdate={can(page.data.user, "leave.request.write", "any")}
        action="?/update"
        error={form?.error ?? null}
        ondone={() => (editOpen = false)}
      />
    {/key}
  {/if}
</Modal>

<!-- Own recurring free days (#107): the same shared surface the manager's modal uses, here
     limited to self-service types. -->
<Modal bind:open={recurringOpen} title={t("leave.recurring.title")}>
  <RecurringDaysManager
    patterns={data.myRecurring}
    types={selfServiceTypes}
    userId={page.data.user?.id ?? ""}
    error={form?.error ?? null}
    generated={form?.recurringSaved ? (form.recurringGenerated ?? 0) : null}
  />
</Modal>

<ConfirmDialog
  bind:open={cancelOpen}
  title={t("leave.requests.cancel")}
  message={t("leave.requests.cancel_confirm")}
  action="?/cancel"
  fields={{ id: cancelId }}
  confirmLabel={t("leave.requests.cancel")}
/>
