<script lang="ts">
  import { enhance } from "$app/forms";
  import { Ban, Check, Pencil, Plus, X } from "@lucide/svelte";

  import { page } from "$app/state";
  import { fmtPeriod } from "$lib/core/format";
  import { can } from "$lib/core/permissions";
  import { t } from "$lib/core/i18n";
  import { navLabel, pageTitle } from "$lib/core/title";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import { labelDotClass } from "$lib/core/ui/colors";
  import { LEAVE_TEAM_COLUMNS } from "$lib/modules/leave/columns";
  import LeaveRequestForm from "$lib/modules/leave/LeaveRequestForm.svelte";
  import LeaveStatusPill from "$lib/modules/leave/LeaveStatusPill.svelte";
  import { fmtHours, typeLabel, type LeaveTypeInfo } from "$lib/modules/leave/format";

  let { data, form } = $props();

  type Request = (typeof data.yearRequests)[number];

  const table = createTableLayout<Request>({
    all: () => LEAVE_TEAM_COLUMNS,
    pref: () => data.table.pref,
    sort: () => data.table.sort,
    cells: () => ({
      employee: employeeCell,
      period: periodCell,
      type: typeCell,
      hours: hoursCell,
      status: statusCell,
    }),
  });

  const types = $derived(data.leaveTypes as LeaveTypeInfo[]);
  const typeById = $derived(Object.fromEntries(types.map((lt) => [lt.id, lt])));
  const trackedTypes = $derived(types.filter((lt) => lt.tracks_balance && lt.active));
  const memberName = $derived(
    Object.fromEntries(data.members.map((m) => [m.user_id, m.full_name || m.email])),
  );
  const memberOptions = $derived(
    data.members.map((m) => ({ value: m.user_id, label: m.full_name || m.email })),
  );
  const hoursByUser = $derived(
    Object.fromEntries(data.profiles.map((p) => [p.user_id, Number(p.hours_per_week)])),
  );

  // Team balances: entitled (entitlements) − approved − pending (year requests), per
  // member × tracked type — computed here from the two lists already loaded, no extra calls.
  interface Cell {
    entitled: number;
    used: number;
    remaining: number;
  }
  const balanceRows = $derived.by(() => {
    const entitled: Record<string, number> = {};
    for (const ent of data.entitlements) {
      const key = `${ent.user_id}|${ent.leave_type_id}`;
      entitled[key] = (entitled[key] ?? 0) + Number(ent.hours);
    }
    const used: Record<string, number> = {};
    for (const request of data.yearRequests) {
      if (request.status !== "approved" && request.status !== "pending") continue;
      const key = `${request.user_id}|${request.leave_type_id}`;
      used[key] = (used[key] ?? 0) + Number(request.hours);
    }
    return data.members.map((member) => {
      const cells: Record<string, Cell> = {};
      for (const lt of trackedTypes) {
        const key = `${member.user_id}|${lt.id}`;
        const ent = entitled[key] ?? 0;
        const use = used[key] ?? 0;
        cells[lt.id] = { entitled: ent, used: use, remaining: ent - use };
      }
      return { member, cells };
    });
  });

  // Approving over-balance leave must be an informed choice (#109): pending already counts
  // against the balance, so a negative remaining *is* "what approving leaves them at".
  const cellByUserType = $derived.by(() => {
    const map: Record<string, Cell> = {};
    for (const row of balanceRows) {
      for (const [typeId, cell] of Object.entries(row.cells)) {
        map[`${row.member.user_id}|${typeId}`] = cell;
      }
    }
    return map;
  });

  function balanceFor(request: Request): Cell | null {
    const type = typeById[request.leave_type_id];
    if (!type?.tracks_balance) return null;
    // The loaded entitlements are year-scoped; a request outside the viewed year has no cell.
    if (Number(request.start_date.slice(0, 4)) !== data.year) return null;
    return cellByUserType[`${request.user_id}|${request.leave_type_id}`] ?? null;
  }

  function overBalanceBy(request: Request): number {
    const cell = balanceFor(request);
    return cell && cell.remaining < 0 ? -cell.remaining : 0;
  }

  let registerOpen = $state(false);
  let rejectId = $state("");
  let rejectOpen = $state(false);
  let cancelId = $state("");
  let cancelOpen = $state(false);

  // Editing a member's request from here (#106): the shared form in a modal, like every other
  // reporting row (docs/UX.md). Gated on `write:any` — approving and editing are separate grants.
  const canEditAny = $derived(can(page.data.user, "leave.request.write", "any"));
  // Deep link from a calendar chip or a "requests leave" notification: resolved once into
  // state initializers (core/edit-intent.ts). A *pending* request opens the review modal —
  // approve/deny one click away, which is what the notification promised — an approved one
  // opens the edit modal. The pending list is cross-year, so a next-year request still lands.
  function deepLinked(): { review: Request | null; edit: Request | null } {
    const id = page.url.searchParams.get("request");
    if (!id) return { review: null, edit: null };
    const review = data.pending.find((r: Request) => r.id === id) ?? null;
    if (review) return { review, edit: null };
    const edit =
      data.yearRequests.find(
        (r: Request) => r.id === id && (r.status === "pending" || r.status === "approved"),
      ) ?? null;
    return { review: null, edit };
  }
  const initial = deepLinked();
  let reviewRequest = $state<Request | null>(initial.review);
  let reviewOpen = $state(initial.review !== null);
  let editRequest = $state<Request | null>(initial.edit);
  let editOpen = $state(initial.edit !== null);

  // --- bulk actions ---------------------------------------------------------------
  // Selection is per page and resets with the rows (DataTable's rule); the eligible subsets
  // are derived from status so a mixed selection simply skips what an action can't touch.
  let bulkSelected = $state<string[]>([]);
  const selectedRows = $derived(
    data.yearRequests.filter((r: Request) => bulkSelected.includes(r.id)),
  );
  const bulkPendingIds = $derived(
    selectedRows.filter((r: Request) => r.status === "pending").map((r: Request) => r.id),
  );
  const bulkCancellableIds = $derived(
    selectedRows
      .filter((r: Request) => r.status === "pending" || r.status === "approved")
      .map((r: Request) => r.id),
  );
  let bulkRejectOpen = $state(false);
  let bulkCancelOpen = $state(false);
  const editTitle = $derived(
    editRequest
      ? `${t("leave.requests.edit")} · ${memberName[editRequest.user_id] ?? ""}`
      : t("leave.requests.edit"),
  );

  function openEdit(request: Request) {
    editRequest = request;
    editOpen = true;
  }

  function period(request: { start_date: string; end_date: string }): string {
    return fmtPeriod(request.start_date, request.end_date);
  }
</script>

<svelte:head>
  <title>{pageTitle(navLabel("leave", t("leave.team.title")))}</title>
</svelte:head>

<div class="mb-4 flex flex-wrap items-center justify-between gap-3">
  <div class="flex items-center gap-3">
    <h1 class="text-xl font-semibold text-text">{navLabel("leave", t("leave.team.title"))}</h1>
    <div class="flex items-center gap-1 text-sm" data-sveltekit-preload-data="hover">
      <a href="?year={data.year - 1}" class="rounded px-1.5 py-0.5 text-text-muted hover:text-brand"
        >‹</a
      >
      <span class="font-medium text-text">{data.year}</span>
      <a href="?year={data.year + 1}" class="rounded px-1.5 py-0.5 text-text-muted hover:text-brand"
        >›</a
      >
    </div>
  </div>
  <button
    type="button"
    class="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
    onclick={() => (registerOpen = true)}
  >
    <Plus size={16} />
    {t("leave.team.register")}
  </button>
</div>

{#if form?.error}
  <p class="mb-4 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
{/if}
{#if form?.bulkDone !== undefined}
  <p class="mb-4 text-sm text-green-600">
    {t("leave.bulk.result", { count: form.bulkDone ?? 0, skipped: form.bulkSkipped ?? 0 })}
  </p>
{/if}

<!-- Pending approvals -->
<section class="mb-6 overflow-hidden rounded-xl border border-border bg-surface-raised">
  <h2
    class="border-b border-border bg-surface px-4 py-2 text-xs font-semibold uppercase tracking-wide text-text-muted"
  >
    {t("leave.team.pending_heading")}
  </h2>
  {#if data.pending.length === 0}
    <p class="p-6 text-sm text-text-muted">{t("leave.team.pending_empty")}</p>
  {:else}
    <ul class="divide-y divide-border">
      {#each data.pending as request (request.id)}
        {@const leaveType = typeById[request.leave_type_id]}
        <li class="flex flex-wrap items-center gap-3 px-4 py-3">
          <div class="min-w-0 flex-1">
            <p class="flex flex-wrap items-center gap-2 text-sm font-medium text-text">
              {memberName[request.user_id] ?? "—"}
              <!-- An edit to previously-approved leave, not new leave (#120). -->
              {#if request.resubmitted_at}
                <span
                  class="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800 dark:bg-amber-900/40 dark:text-amber-300"
                >
                  {t("leave.team.resubmitted")}
                </span>
              {/if}
            </p>
            <p class="mt-0.5 flex flex-wrap items-center gap-x-2 text-sm text-text-muted">
              <span class="inline-flex items-center gap-1.5">
                <span class="h-2 w-2 rounded-full {labelDotClass(leaveType?.color ?? '')}"></span>
                {typeLabel(leaveType, data.locale)}
              </span>
              <span>{period(request)}</span>
              <span class="tabular-nums">
                {t("leave.team.hours_amount", { hours: fmtHours(request.hours) })}
              </span>
              <!-- The decision is self-contained (#119): the still-available balance sits on the
                   row instead of in the grid below; the amber warning owns the negative case. -->
              {#if balanceFor(request) && overBalanceBy(request) === 0}
                {@const cell = balanceFor(request)}
                <span class="tabular-nums text-xs">
                  {t("leave.team.balance_left", {
                    left: fmtHours(cell?.remaining ?? 0),
                    total: fmtHours(cell?.entitled ?? 0),
                  })}
                </span>
              {/if}
            </p>
            {#if request.note}
              <p class="mt-0.5 truncate text-xs text-text-muted">{request.note}</p>
            {/if}
            {#if overBalanceBy(request) > 0}
              <p class="mt-0.5 text-xs font-medium text-amber-600 dark:text-amber-400">
                {t("leave.team.over_balance", { hours: fmtHours(overBalanceBy(request)) })}
              </p>
            {/if}
          </div>
          <div class="flex items-center gap-2">
            <form method="POST" action="?/decide" use:enhance>
              <input type="hidden" name="id" value={request.id} />
              <input type="hidden" name="approved" value="true" />
              <button
                class="flex items-center gap-1.5 rounded-lg bg-green-600 px-3 py-1.5 text-sm font-medium text-white hover:opacity-90"
              >
                <Check size={14} />
                {t("leave.team.approve")}
              </button>
            </form>
            <button
              type="button"
              class="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-text hover:border-red-400 hover:text-red-600 dark:hover:border-red-500 dark:hover:text-red-400"
              onclick={() => {
                rejectId = request.id;
                rejectOpen = true;
              }}
            >
              <X size={14} />
              {t("leave.team.reject")}
            </button>
          </div>
        </li>
      {/each}
    </ul>
  {/if}
</section>

<!-- Team balances -->
<section class="mb-6 overflow-hidden rounded-xl border border-border bg-surface-raised">
  <h2
    class="border-b border-border bg-surface px-4 py-2 text-xs font-semibold uppercase tracking-wide text-text-muted"
  >
    {t("leave.team.balances_heading", { year: data.year })}
  </h2>
  <div class="overflow-x-auto">
    <table class="w-full text-sm">
      <thead>
        <tr class="border-b border-border text-left text-xs text-text-muted">
          <th class="px-4 py-2 font-medium">{t("leave.team.member")}</th>
          <th class="px-2 py-2 text-right font-medium">{t("leave.team.contract_hours")}</th>
          {#each trackedTypes as lt (lt.id)}
            <th class="px-2 py-2 text-right font-medium">{typeLabel(lt, data.locale)}</th>
          {/each}
        </tr>
      </thead>
      <tbody class="divide-y divide-border">
        {#each balanceRows as row (row.member.user_id)}
          <tr>
            <td class="px-4 py-2 font-medium text-text">
              {row.member.full_name || row.member.email}
            </td>
            <td class="px-2 py-2 text-right tabular-nums text-text-muted">
              {fmtHours(hoursByUser[row.member.user_id] ?? 40)}
            </td>
            {#each trackedTypes as lt (lt.id)}
              {@const cell = row.cells[lt.id]}
              <td class="px-2 py-2 text-right tabular-nums">
                <span
                  class={(cell?.remaining ?? 0) < 0
                    ? "font-medium text-red-600 dark:text-red-400"
                    : "text-text"}
                >
                  {fmtHours(cell?.remaining ?? 0)}
                </span>
                <span class="text-xs text-text-muted">/ {fmtHours(cell?.entitled ?? 0)}</span>
              </td>
            {/each}
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
</section>

<!-- All requests this year -->
{#snippet employeeCell(request: Request)}
  <span class="font-medium text-text">{memberName[request.user_id] ?? "—"}</span>
{/snippet}

{#snippet periodCell(request: Request)}
  <span class="text-text">{period(request)}</span>
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

{#snippet statusCell(request: Request)}
  <LeaveStatusPill status={request.status} />
{/snippet}

{#snippet teamRowActions(request: Request)}
  {#if request.status === "pending" || request.status === "approved"}
    <ActionsMenu
      compact
      items={[
        ...(canEditAny
          ? [{ label: t("common.edit"), icon: Pencil, onclick: () => openEdit(request) }]
          : []),
        {
          label: t("leave.requests.cancel"),
          icon: Ban,
          danger: true,
          onclick: () => {
            cancelId = request.id;
            cancelOpen = true;
          },
        },
      ]}
    />
  {/if}
{/snippet}

{#snippet teamMobileRow(request: Request)}
  <div class="flex items-center gap-3">
    <span class="min-w-0 flex-1">
      <span class="font-medium text-text">{memberName[request.user_id] ?? "—"}</span>
      <span class="mt-0.5 block text-sm text-text-muted">
        {period(request)} · {fmtHours(request.hours)}
      </span>
    </span>
    <LeaveStatusPill status={request.status} />
    {@render teamRowActions(request)}
  </div>
{/snippet}

{#snippet teamEmpty()}
  <p class="rounded-xl border border-border bg-surface-raised p-6 text-sm text-text-muted">
    {t("leave.requests.empty")}
  </p>
{/snippet}

<div class="mb-2 mt-6 flex items-center justify-between">
  <h2 class="text-xs font-semibold uppercase tracking-wide text-text-muted">
    {t("leave.team.requests_heading", { year: data.year })}
  </h2>
  <ColumnPicker
    all={table.pickerColumns}
    visible={table.visibleKeys}
    sort={table.sort}
    onchange={table.onColumnsChange}
    onsort={table.onSort}
  />
</div>

{#snippet bulkBar(ids: string[])}
  <span class="text-xs font-medium text-text">{t("table.selected", { count: ids.length })}</span>
  <form method="POST" action="?/bulkDecide" use:enhance>
    {#each bulkPendingIds as id (id)}
      <input type="hidden" name="ids" value={id} />
    {/each}
    <input type="hidden" name="approved" value="true" />
    <button
      disabled={bulkPendingIds.length === 0}
      class="flex items-center gap-1.5 rounded-lg bg-green-600 px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
    >
      <Check size={13} />
      {t("leave.team.approve")}
    </button>
  </form>
  <button
    type="button"
    disabled={bulkPendingIds.length === 0}
    class="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs text-text hover:border-red-400 hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-40 dark:hover:border-red-500 dark:hover:text-red-400"
    onclick={() => (bulkRejectOpen = true)}
  >
    <X size={13} />
    {t("leave.team.reject")}
  </button>
  <button
    type="button"
    disabled={bulkCancellableIds.length === 0}
    class="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs text-text-muted hover:text-text disabled:cursor-not-allowed disabled:opacity-40"
    onclick={() => (bulkCancelOpen = true)}
  >
    <Ban size={13} />
    {t("leave.requests.cancel")}
  </button>
{/snippet}

<DataTable
  rows={data.yearRequests}
  columns={table.columns}
  sort={table.sort}
  widths={table.widths}
  locale={data.locale}
  actions={teamRowActions}
  mobileRow={teamMobileRow}
  empty={teamEmpty}
  selectable
  bind:selected={bulkSelected}
  selection={bulkBar}
  onsort={table.onSort}
  onresize={table.onResize}
/>

<ConfirmDialog
  bind:open={bulkRejectOpen}
  title={t("leave.team.reject")}
  message={t("leave.bulk.reject_confirm")}
  action="?/bulkDecide"
  fields={{ ids: bulkPendingIds.join(","), approved: "false" }}
  confirmLabel={t("leave.team.reject")}
/>

<ConfirmDialog
  bind:open={bulkCancelOpen}
  title={t("leave.requests.cancel")}
  message={t("leave.bulk.cancel_confirm")}
  action="?/bulkCancel"
  fields={{ ids: bulkCancellableIds.join(",") }}
  confirmLabel={t("leave.requests.cancel")}
/>

<!-- Register leave for a team member (sick call, phoned-in request) -->
<Modal bind:open={registerOpen} title={t("leave.team.register")}>
  <!-- This page is already gated on `leave.request.approve`, which is exactly who may set the
       hours by hand — for the day an employee agrees to four hours they were not scheduled. -->
  <LeaveRequestForm
    types={types.filter((lt) => lt.active)}
    userOptions={memberOptions}
    canOverride
    canBackdate={can(page.data.user, "leave.request.write", "any")}
    action="?/register"
    error={form?.error ?? null}
    ondone={() => (registerOpen = false)}
  />
</Modal>

<!-- Review a pending request (notification deep-link): the details and the decision on one
     surface, instead of dumping the approver on a page to go hunting for the row. -->
<Modal bind:open={reviewOpen} title={t("leave.review.title")}>
  {#if reviewRequest}
    {@const leaveType = typeById[reviewRequest.leave_type_id]}
    <div class="space-y-4">
      <div>
        <p class="flex flex-wrap items-center gap-2 text-sm font-medium text-text">
          {memberName[reviewRequest.user_id] ?? "—"}
          {#if reviewRequest.resubmitted_at}
            <span
              class="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800 dark:bg-amber-900/40 dark:text-amber-300"
            >
              {t("leave.team.resubmitted")}
            </span>
          {/if}
        </p>
        <p class="mt-1 flex flex-wrap items-center gap-x-2 text-sm text-text-muted">
          <span class="inline-flex items-center gap-1.5">
            <span class="h-2 w-2 rounded-full {labelDotClass(leaveType?.color ?? '')}"></span>
            {typeLabel(leaveType, data.locale)}
          </span>
          <span>{period(reviewRequest)}</span>
          <span class="tabular-nums">
            {t("leave.team.hours_amount", { hours: fmtHours(reviewRequest.hours) })}
          </span>
          {#if balanceFor(reviewRequest) && overBalanceBy(reviewRequest) === 0}
            {@const cell = balanceFor(reviewRequest)}
            <span class="tabular-nums text-xs">
              {t("leave.team.balance_left", {
                left: fmtHours(cell?.remaining ?? 0),
                total: fmtHours(cell?.entitled ?? 0),
              })}
            </span>
          {/if}
        </p>
        {#if reviewRequest.note}
          <p class="mt-1 text-sm text-text-muted">{reviewRequest.note}</p>
        {/if}
        {#if overBalanceBy(reviewRequest) > 0}
          <p class="mt-1 text-xs font-medium text-amber-600 dark:text-amber-400">
            {t("leave.team.over_balance", { hours: fmtHours(overBalanceBy(reviewRequest)) })}
          </p>
        {/if}
      </div>
      <div class="flex justify-end gap-2">
        <button
          type="button"
          class="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-text hover:border-red-400 hover:text-red-600 dark:hover:border-red-500 dark:hover:text-red-400"
          onclick={() => {
            rejectId = reviewRequest?.id ?? "";
            reviewOpen = false;
            rejectOpen = true;
          }}
        >
          <X size={14} />
          {t("leave.team.reject")}
        </button>
        <form
          method="POST"
          action="?/decide"
          use:enhance={() =>
            ({ result, update }) => {
              if (result.type === "success") reviewOpen = false;
              void update();
            }}
        >
          <input type="hidden" name="id" value={reviewRequest.id} />
          <input type="hidden" name="approved" value="true" />
          <button
            class="flex items-center gap-1.5 rounded-lg bg-green-600 px-3 py-1.5 text-sm font-medium text-white hover:opacity-90"
          >
            <Check size={14} />
            {t("leave.team.approve")}
          </button>
        </form>
      </div>
    </div>
  {/if}
</Modal>

<!-- Edit a member's request (#106): same shared form; the API decides re-approval (#72). -->
<Modal bind:open={editOpen} title={editTitle}>
  {#if editRequest}
    {#key editRequest.id}
      <LeaveRequestForm
        types={types.filter((lt) => lt.active)}
        request={editRequest}
        canOverride
        canBackdate={canEditAny}
        action="?/update"
        error={form?.error ?? null}
        ondone={() => (editOpen = false)}
      />
    {/key}
  {/if}
</Modal>

<!-- Reject with an optional reason -->
<Modal bind:open={rejectOpen} title={t("leave.team.reject")}>
  <form
    method="POST"
    action="?/decide"
    class="space-y-4"
    use:enhance={() =>
      ({ update }) => {
        rejectOpen = false;
        void update();
      }}
  >
    <input type="hidden" name="id" value={rejectId} />
    <input type="hidden" name="approved" value="false" />
    <div>
      <label class="mb-1 block text-xs font-medium text-text-muted" for="reject-note">
        {t("leave.team.reject_reason")}
      </label>
      <textarea
        id="reject-note"
        name="note"
        rows="2"
        class="w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
      ></textarea>
    </div>
    <div class="flex justify-end gap-2">
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm"
        onclick={() => (rejectOpen = false)}>{t("common.cancel")}</button
      >
      <button
        class="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:opacity-90"
      >
        {t("leave.team.reject")}
      </button>
    </div>
  </form>
</Modal>

<ConfirmDialog
  bind:open={cancelOpen}
  title={t("leave.requests.cancel")}
  message={t("leave.requests.cancel_confirm")}
  action="?/cancel"
  fields={{ id: cancelId }}
  confirmLabel={t("leave.requests.cancel")}
/>
