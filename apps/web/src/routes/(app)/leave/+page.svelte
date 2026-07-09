<script lang="ts">
  import { Ban, Pencil, Plus } from "@lucide/svelte";

  import { fmtDayMonth } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import { labelDotClass } from "$lib/core/ui/colors";
  import LeaveRequestForm from "$lib/modules/leave/LeaveRequestForm.svelte";
  import LeaveStatusPill from "$lib/modules/leave/LeaveStatusPill.svelte";
  import { fmtHours, hoursToDays, typeLabel, type LeaveTypeInfo } from "$lib/modules/leave/format";

  let { data, form } = $props();

  const types = $derived(data.leaveTypes as LeaveTypeInfo[]);
  const typeById = $derived(Object.fromEntries(types.map((lt) => [lt.id, lt])));
  const remainingByType = $derived(
    Object.fromEntries(data.balances.map((b) => [b.leave_type_id, Number(b.remaining_hours)])),
  );

  let createOpen = $state(false);
  let editOpen = $state(false);
  let editRequest = $state<(typeof data.requests)[number] | null>(null);
  let cancelId = $state("");
  let cancelOpen = $state(false);

  function openEdit(request: (typeof data.requests)[number]) {
    editRequest = request;
    editOpen = true;
  }

  function period(request: { start_date: string; end_date: string }): string {
    return request.start_date === request.end_date
      ? fmtDayMonth(request.start_date)
      : `${fmtDayMonth(request.start_date)} – ${fmtDayMonth(request.end_date)}`;
  }

  const yearLink = (year: number) => `?year=${year}`;
</script>

<svelte:head>
  <title>{t("leave.title")}</title>
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
  <button
    type="button"
    class="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
    onclick={() => (createOpen = true)}
  >
    <Plus size={16} />
    {t("leave.request_button")}
  </button>
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
          days: fmtHours(hoursToDays(balance.remaining_hours, data.hoursPerWeek)),
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

<!-- My requests -->
<section class="overflow-hidden rounded-xl border border-border bg-surface-raised">
  <h2
    class="border-b border-border bg-surface px-4 py-2 text-xs font-semibold uppercase tracking-wide text-text-muted"
  >
    {t("leave.requests.heading")}
  </h2>
  {#if data.requests.length === 0}
    <p class="p-6 text-sm text-text-muted">{t("leave.requests.empty")}</p>
  {:else}
    <div class="overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-border text-left text-xs text-text-muted">
            <th class="px-4 py-2 font-medium">{t("leave.requests.period")}</th>
            <th class="px-4 py-2 font-medium">{t("leave.form.type")}</th>
            <th class="px-4 py-2 text-right font-medium">{t("leave.form.hours")}</th>
            <th class="px-4 py-2 font-medium">{t("leave.requests.status")}</th>
            <th class="px-2 py-2"></th>
          </tr>
        </thead>
        <tbody class="divide-y divide-border">
          {#each data.requests as request (request.id)}
            {@const leaveType = typeById[request.leave_type_id]}
            {@const pending = request.status === "pending"}
            <tr>
              <td class="px-4 py-2 font-medium text-text">
                {period(request)}
                {#if request.note}
                  <p class="mt-0.5 max-w-[16rem] truncate text-xs font-normal text-text-muted">
                    {request.note}
                  </p>
                {/if}
              </td>
              <td class="px-4 py-2">
                <span class="inline-flex items-center gap-1.5 text-text">
                  <span class="h-2 w-2 rounded-full {labelDotClass(leaveType?.color ?? '')}"></span>
                  {typeLabel(leaveType, data.locale)}
                </span>
              </td>
              <td class="px-4 py-2 text-right tabular-nums text-text">
                {fmtHours(request.hours)}
              </td>
              <td class="px-4 py-2">
                <LeaveStatusPill status={request.status} />
                {#if request.status === "rejected" && request.decision_note}
                  <p class="mt-0.5 max-w-[14rem] truncate text-xs text-text-muted">
                    {request.decision_note}
                  </p>
                {/if}
              </td>
              <td class="px-2 py-2 text-right">
                {#if pending}
                  <ActionsMenu
                    compact
                    items={[
                      {
                        label: t("common.edit"),
                        icon: Pencil,
                        onclick: () => openEdit(request),
                      },
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
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</section>

<Modal bind:open={createOpen} title={t("leave.request_button")}>
  <LeaveRequestForm
    types={types.filter((lt) => lt.active)}
    hoursPerWeek={data.hoursPerWeek}
    balances={remainingByType}
    error={form?.error ?? null}
    ondone={() => (createOpen = false)}
  />
</Modal>

<Modal bind:open={editOpen} title={t("leave.requests.edit")}>
  {#if editRequest}
    {#key editRequest.id}
      <LeaveRequestForm
        types={types.filter((lt) => lt.active)}
        hoursPerWeek={data.hoursPerWeek}
        balances={remainingByType}
        request={editRequest}
        action="?/update"
        error={form?.error ?? null}
        ondone={() => (editOpen = false)}
      />
    {/key}
  {/if}
</Modal>

<ConfirmDialog
  bind:open={cancelOpen}
  title={t("leave.requests.cancel")}
  message={t("leave.requests.cancel_confirm")}
  action="?/cancel"
  fields={{ id: cancelId }}
  confirmLabel={t("leave.requests.cancel")}
/>
