<script lang="ts">
  import { enhance } from "$app/forms";
  import { Ban, Check, Plus, X } from "@lucide/svelte";

  import { fmtDayMonth } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
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

  let registerOpen = $state(false);
  let rejectId = $state("");
  let rejectOpen = $state(false);
  let cancelId = $state("");
  let cancelOpen = $state(false);

  function period(request: { start_date: string; end_date: string }): string {
    return request.start_date === request.end_date
      ? fmtDayMonth(request.start_date)
      : `${fmtDayMonth(request.start_date)} – ${fmtDayMonth(request.end_date)}`;
  }
</script>

<svelte:head>
  <title>{t("leave.team.title")}</title>
</svelte:head>

<div class="mb-4 flex flex-wrap items-center justify-between gap-3">
  <div class="flex items-center gap-3">
    <h1 class="text-xl font-semibold text-text">{t("leave.team.title")}</h1>
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
            <p class="text-sm font-medium text-text">
              {memberName[request.user_id] ?? "—"}
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
            </p>
            {#if request.note}
              <p class="mt-0.5 truncate text-xs text-text-muted">{request.note}</p>
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

<DataTable
  rows={data.yearRequests}
  columns={table.columns}
  sort={table.sort}
  widths={table.widths}
  locale={data.locale}
  actions={teamRowActions}
  mobileRow={teamMobileRow}
  empty={teamEmpty}
  onsort={table.onSort}
  onresize={table.onResize}
/>

<!-- Register leave for a team member (sick call, phoned-in request) -->
<Modal bind:open={registerOpen} title={t("leave.team.register")}>
  <!-- This page is already gated on `leave.request.approve`, which is exactly who may set the
       hours by hand — for the day an employee agrees to four hours they were not scheduled. -->
  <LeaveRequestForm
    types={types.filter((lt) => lt.active)}
    userOptions={memberOptions}
    canOverride
    action="?/register"
    error={form?.error ?? null}
    ondone={() => (registerOpen = false)}
  />
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
