<script lang="ts">
  import { Ban, CalendarClock, Pencil, Plus, Repeat } from "@lucide/svelte";

  import { page } from "$app/state";
  import { fmtPeriod } from "$lib/core/format";
  import { can } from "$lib/core/permissions";
  import { t } from "$lib/core/i18n";
  import { navLabel, pageTitle } from "$lib/core/title";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import BulkBar from "$lib/core/bulk/BulkBar.svelte";
  import BulkToggle from "$lib/core/bulk/BulkToggle.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import Pagination from "$lib/core/ui/Pagination.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import { labelDotClass } from "$lib/core/ui/colors";
  import { LEAVE_COLUMNS } from "$lib/modules/leave/columns";
  import FreeTimeCard from "$lib/modules/leave/FreeTimeCard.svelte";
  import LeaveRequestForm from "$lib/modules/leave/LeaveRequestForm.svelte";
  import LeaveStatusPill from "$lib/modules/leave/LeaveStatusPill.svelte";
  import AvailabilityManager from "$lib/modules/leave/AvailabilityManager.svelte";
  import RecurringDaysManager from "$lib/modules/leave/RecurringDaysManager.svelte";
  import {
    fmtHours,
    hoursToDays,
    typeLabel,
    GROUP_LABEL_KEYS,
    type LeaveTypeInfo,
  } from "$lib/modules/leave/format";

  let { data, form } = $props();

  type Request = (typeof data.requests)[number];
  type GroupBalance = (typeof data.groups)[number];

  /** The combined balance's label: the message-catalog copy for a known group (#265), else the
   *  API/representative label the server already resolved for a tenant's own group. */
  function groupLabel(group: GroupBalance): string {
    const key = group.group ? GROUP_LABEL_KEYS[group.group] : undefined;
    if (key) return t(key);
    const l = group.label_i18n as Record<string, string>;
    return l[data.locale] ?? l.nl ?? l.en ?? Object.values(l)[0] ?? "";
  }

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
  // Is the period in force a freelance one? Three things on this page turn on it, so it is
  // resolved once, up here: a freelancer accrues nothing, which makes an empty balance tile and
  // a free-time planner two ways of saying "does not apply" in the language of "you have none
  // left". The *kind* is a question only the server can answer (`LeaveProfileRead
  // .employment_type`, `null` when no period is on file — a tenant with no contracts shows the
  // freelance surfaces to nobody rather than to everybody).
  const isFreelance = $derived(data.employmentType === "freelance");
  // Free time has its own card; showing its group tile as well means the page states the same
  // balance twice, once uselessly (see the markup).
  const freeTimeIds = $derived(new Set(data.freeTime?.leave_type_ids ?? []));
  const balanceGroups = $derived(
    data.groups
      .filter((g) => !g.leave_type_ids.some((id) => freeTimeIds.has(id)))
      // A freelance period accrues nothing, so an empty pot is "does not apply" and not "you
      // have none left" — and a tile reading "Vakantieverlof 0 u · van 0 u" says the second.
      // Only the empty ones go: a hand-granted pot (the escape hatch for a negotiated
      // arrangement) is non-zero and stays exactly where an employee's would be.
      .filter((g) => !isFreelance || Number(g.entitled_hours) > 0),
  );
  // Remaining keyed by *every* type in a group → the group's combined remaining (#265), so the
  // request form's balance hint reads the combined pool whichever underlying type it posts to.
  const remainingByType = $derived(
    Object.fromEntries(
      data.groups.flatMap((g) =>
        g.leave_type_ids.map((id) => [id, Number(g.remaining_hours)] as const),
      ),
    ),
  );

  // `?new=1` opens the create modal on arrival — the deep link the calendar "+" points at
  // (#188), mirroring the `?request=` edit deep link. A `$state` initializer, not a `$derived`:
  // it opens on load and the user can then close it.
  let createOpen = $state(page.url.searchParams.get("new") === "1");
  // Recurring free days, self-service (#107): balance-tracked auto-approve types only. The
  // approval requirement keeps vacation a manager's act (generated days are pre-approved);
  // the balance requirement keeps "sick" out — a *recurring sick day* is not a plan, and a
  // pot is what bounds how much a pattern may hand out. The API enforces both.
  //
  // None of it for a freelance period: every such type draws on a pot, and a freelance period
  // accrues none — so the button opened a planner that could only ever hand out days the
  // balance refuses. Availability is the surface that answers the same question for them.
  const selfServiceTypes = $derived(
    isFreelance
      ? []
      : types.filter((lt) => lt.active && !lt.requires_approval && lt.tracks_balance),
  );
  let recurringOpen = $state(false);

  // Availability (freelance): the days on top of the week you were engaged under, as a
  // **section** rather than a button behind a modal. Every member holds
  // `leave.availability.write:own`, so gating on the permission alone put a control on every
  // employee's page for a thing employees do not have. The permission still gates the writes
  // (it is the API's own key, mirrored); the kind decides whether the surface exists at all.
  const showAvailability = $derived(
    isFreelance && data.myAvailability !== null && can(page.data.user, "leave.availability.write"),
  );
  // Deep link from an agenda chip (#106's shape): `?availability=<id>` scrolls the row into view
  // and marks it, the way `?request=` opens a request. Resolved once, into a state initializer —
  // the surface reacts on load and the user then moves on.
  const highlightAvailability = page.url.searchParams.get("availability") ?? "";

  // Bulk cancel: the one bulk act your own list has — everything else is per-request.
  // The ✎ mode, like every other list (#353). This screen used to draw a checkbox column from
  // first paint and offer no way to close it, so two of sixteen lists behaved differently from
  // the fourteen a user had already learned (CLAUDE.md §18).
  let selecting = $state(false);
  let bulkSelected = $state<string[]>([]);
  const bulkCancellableIds = $derived(
    data.requests
      .filter((r: Request) => bulkSelected.includes(r.id) && canCancel(r))
      .map((r: Request) => r.id),
  );
  let bulkCancelOpen = $state(false);
  // One configuration, spread into the ✎ and the strip above the table. `eligible` is what turns
  // "greyed out, no reason given" into a sentence: six of nine rows on this list are cancelled
  // free-time days, so a selection that can do nothing is the common case, not the edge one.
  const bulkConfig = $derived({
    items: [
      {
        label: t("leave.requests.cancel"),
        icon: Ban,
        danger: true,
        eligible: bulkCancellableIds.length,
        disabledReason:
          bulkCancellableIds.length === 0 ? t("leave.bulk.cancel_none") : undefined,
        onclick: () => (bulkCancelOpen = true),
      },
    ],
  });
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

  /** "Verplaats" on a free day is an ordinary edit of its request — same modal, same rules. */
  function openById(id: string) {
    const match = data.requests.find((r: Request) => r.id === id);
    if (match) openEdit(match);
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
    return fmtPeriod(request.start_date, request.end_date);
  }

  const yearLink = (year: number) => `?year=${year}`;
</script>

<svelte:head>
  <title>{pageTitle(navLabel("leave", t("leave.title")))}</title>
</svelte:head>

<div class="mb-4 flex flex-wrap items-center justify-between gap-3">
  <div class="flex items-center gap-3">
    <h1 class="text-xl font-semibold text-text">{navLabel("leave", t("leave.title"))}</h1>
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

<!-- The free-time modal closes on a successful add (#271), so its "N days placed" line lands here,
     where it outlives the surface that produced it — and sits right above the balances the
     new days just moved. -->
{#if form?.recurringAdded}
  <p class="mb-4 text-sm text-green-600 dark:text-green-400">
    {t("leave.recurring.generated", { count: form.recurringGenerated ?? 0 })}
  </p>
{/if}

<!-- One balance per group: statutory + extra-statutory vacation read as one "Vakantieverlof"
     figure (#265); each pot's expiry is folded into the lapsed / expiring-soon hints.
     Free time is deliberately **not** among them: its own card below answers the same question
     properly, and the tile version ("4 u over" under a calendar full of placed days) is exactly
     the useless number that card exists to replace. Two of them side by side is worse than one. -->
<div class="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
  {#each balanceGroups as group (group.leave_type_ids.join(","))}
    {@const color = typeById[group.leave_type_ids[0]]?.color ?? ""}
    <div class="rounded-xl border border-border bg-surface-raised p-5">
      <div class="mb-2 flex items-center gap-2">
        <span class="h-2.5 w-2.5 rounded-full {labelDotClass(color)}"></span>
        <h2 class="text-sm font-semibold text-text">{groupLabel(group)}</h2>
      </div>
      <p
        class="text-2xl font-semibold {Number(group.remaining_hours) < 0
          ? 'text-red-600 dark:text-red-400'
          : 'text-text'}"
      >
        {t("leave.balance.remaining", { hours: fmtHours(group.remaining_hours) })}
      </p>
      <p class="mt-1 text-sm text-text-muted">
        {t("leave.balance.days_equiv", {
          days: fmtHours(hoursToDays(group.remaining_hours, data.hoursPerDay)),
        })}
        · {t("leave.balance.of_total", { hours: fmtHours(group.entitled_hours) })}
      </p>
      {#if Number(group.pending_hours) > 0}
        <p class="mt-1 text-xs text-amber-600 dark:text-amber-400">
          {t("leave.balance.pending", { hours: fmtHours(group.pending_hours) })}
        </p>
      {/if}
      {#if Number(group.expiring_soon_hours) > 0}
        <p class="mt-1 text-xs text-amber-600 dark:text-amber-400">
          {t("leave.balance.expiring", { hours: fmtHours(group.expiring_soon_hours) })}
        </p>
      {/if}
      {#if Number(group.lapsed_hours) > 0}
        <p class="mt-1 text-xs text-text-muted">
          {t("leave.balance.lapsed", { hours: fmtHours(group.lapsed_hours) })}
        </p>
      {/if}
    </div>
  {:else}
    <!-- "An admin sets these up" is a to-do list for an employee and a wrong answer for a
         freelancer: nothing is missing, a freelance period simply accrues nothing. Saying it
         anyway renders a state we exist to serve as a fault.
         (An `{#each}`'s `{:else}` takes no `if` of its own — that is `{#if}`'s alone.) -->
    {#if !isFreelance}
      <p
        class="rounded-xl border border-border bg-surface-raised p-5 text-sm text-text-muted sm:col-span-2 lg:col-span-3"
      >
        {t("leave.balance.none")}
      </p>
    {/if}
  {/each}
</div>

<!-- Free time gets its own card rather than a fourth balance tile: its balance reads "0 u over"
     as soon as the days are placed, and the question people actually have is when the next one
     is and whether they can move it (#65). -->
<FreeTimeCard
  freeTime={data.freeTime}
  color={typeById[data.freeTime?.leave_type_ids?.[0] ?? ""]?.color ?? "cyan"}
  onmove={openById}
  oncancel={(id) => {
    cancelId = id;
    cancelOpen = true;
  }}
/>

<!-- Availability, for a freelancer, as a section of their own page rather than a control hidden
     behind a button: the days you will and will not work are the thing a freelancer keeps here,
     and a surface that has to be found is a surface that is not kept up to date. -->
{#if showAvailability}
  <section class="mb-6 rounded-xl border border-border bg-surface-raised p-5">
    <div class="mb-1 flex flex-wrap items-baseline justify-between gap-2">
      <h2 class="flex items-center gap-2 text-sm font-semibold text-text">
        <CalendarClock size={16} />
        {t("leave.availability.title")}
      </h2>
    </div>
    <p class="mb-3 text-sm text-text-muted">{t("leave.availability.intro")}</p>
    <AvailabilityManager
      entries={data.myAvailability ?? []}
      error={form?.error ?? null}
      highlightId={highlightAvailability}
    />
  </section>
{/if}

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

{#if form?.bulkDone !== undefined}
  <p class="mb-4 text-sm text-green-600">
    {t("leave.bulk.result", { count: form.bulkDone ?? 0, skipped: form.bulkSkipped ?? 0 })}
  </p>
{/if}

<!-- My requests -->
<div class="mb-2 flex items-center justify-between">
  <h2 class="text-xs font-semibold uppercase tracking-wide text-text-muted">
    {t("leave.requests.heading")}
  </h2>
  <div class="flex items-center gap-2">
    <ColumnPicker
      all={table.pickerColumns}
      visible={table.visibleKeys}
      sort={table.sort}
      onchange={table.onColumnsChange}
      onsort={table.onSort}
    />
    <!-- Last in the toolbar, always (CLAUDE.md §18). -->
    <BulkToggle bind:selecting bind:selected={bulkSelected} {...bulkConfig} />
  </div>
</div>

<BulkBar {selecting} bind:selected={bulkSelected} {...bulkConfig} />

<DataTable
  rows={data.requests}
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
  total={data.requestsTotal}
  page={data.paging.page}
  limit={data.paging.limit}
  onsize={table.onPageSize}
/>

<ConfirmDialog
  bind:open={bulkCancelOpen}
  title={t("leave.requests.cancel")}
  message={t("leave.bulk.cancel_confirm")}
  action="?/bulkCancel"
  fields={{ ids: bulkCancellableIds.join(",") }}
  confirmLabel={t("leave.requests.cancel")}
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
    generated={form?.recurringSaved && !form.recurringAdded && !form.patternDeleted
      ? (form.recurringGenerated ?? 0)
      : null}
    deleted={form?.patternDeleted ? { withdrawn: form.withdrawn ?? 0 } : null}
    ondone={() => (recurringOpen = false)}
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
