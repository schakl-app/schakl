<script lang="ts">
  import { CircleCheck, Receipt } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import { fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import { formatMinutes, formatTime } from "$lib/modules/time/format";

  let { data, form } = $props();

  const report = $derived(data.report);
  const entries = $derived(report?.items ?? []);
  const totals = $derived(report?.totals ?? null);

  const memberName = (id?: string | null) => {
    const m = data.members.find((mm) => mm.user_id === id);
    return m ? m.full_name || m.email : "—";
  };
  const companyName = (id?: string | null) => data.companies.find((c) => c.id === id)?.name ?? "";
  const projectName = (id?: string | null) => data.projects.find((p) => p.id === id)?.name ?? "";
  const taskTitle = (id?: string | null) => data.tasks.find((tk) => tk.id === id)?.title ?? "";
  function entryLabel(e: {
    company_id?: string | null;
    project_id?: string | null;
    task_id?: string | null;
  }) {
    const parts = [companyName(e.company_id), projectName(e.project_id), taskTitle(e.task_id)].filter(Boolean);
    return parts.length ? parts.join(" · ") : t("time.general");
  }

  // --- filters (query params → SSR reload) ------------------------------------
  const statuses = ["open", "approved", "to_invoice", "invoiced"] as const;
  function setFilter(key: string, value: string) {
    const url = new URL(page.url);
    if (value) url.searchParams.set(key, value);
    else url.searchParams.delete(key);
    void goto(url, { keepFocus: true, noScroll: true });
  }

  // --- selection ---------------------------------------------------------------
  let selected = $state<Record<string, boolean>>({});
  const selectedIds = $derived(entries.filter((e) => selected[e.id]).map((e) => e.id));
  const allSelected = $derived(entries.length > 0 && selectedIds.length === entries.length);
  function toggleAll() {
    const next: Record<string, boolean> = {};
    if (!allSelected) for (const e of entries) next[e.id] = true;
    selected = next;
  }
  // Reset the selection when the filtered set changes.
  $effect(() => {
    void entries;
    selected = {};
  });

  const inputClass =
    "rounded-lg border border-neutral-300 px-2 py-1.5 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
  const bulkClass =
    "rounded-lg border border-neutral-300 px-3 py-1.5 text-xs font-medium text-neutral-600 hover:border-brand hover:text-brand disabled:cursor-not-allowed disabled:opacity-40";
</script>

<svelte:head>
  <title>{t("time.overview.title")}</title>
</svelte:head>

<div class="mb-4">
  <h1 class="text-xl font-semibold text-neutral-900">{t("time.overview.title")}</h1>
  <p class="mt-1 text-sm text-neutral-500">{t("time.overview.subtitle")}</p>
</div>

<!-- Totals -->
{#if totals}
  <div class="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-5">
    {#each [
      { key: "minutes", value: totals.minutes, accent: "" },
      { key: "billable", value: totals.billable_minutes, accent: "" },
      { key: "open", value: totals.open_minutes, accent: totals.open_minutes ? "text-amber-600" : "" },
      { key: "to_invoice", value: totals.to_invoice_minutes, accent: totals.to_invoice_minutes ? "text-brand" : "" },
      { key: "invoiced", value: totals.invoiced_minutes, accent: "text-green-600" }
    ] as card (card.key)}
      <div class="rounded-xl border border-neutral-200 bg-white p-4">
        <p class="text-xs text-neutral-500">{t(`time.overview.total.${card.key}`)}</p>
        <p class="mt-1 text-lg font-semibold tabular-nums {card.accent || 'text-neutral-900'}">
          {formatMinutes(card.value)}
        </p>
      </div>
    {/each}
  </div>
{/if}

<!-- Filters -->
<div class="mb-4 flex flex-wrap items-center gap-2">
  <div class="w-44">
    <Combobox
      items={data.members.map((m) => ({ value: m.user_id, label: m.full_name || m.email }))}
      name="_f_user" id="f-user" value={data.filters.user_id}
      placeholder={t("time.overview.employee")}
      onselect={(v) => setFilter("user_id", v)}
    />
  </div>
  <div class="w-44">
    <Combobox
      items={data.companies.map((c) => ({ value: c.id, label: c.name }))}
      name="_f_company" id="f-company" value={data.filters.company_id}
      placeholder={t("time.field.company")}
      onselect={(v) => setFilter("company_id", v)}
    />
  </div>
  <div class="w-44">
    <Combobox
      items={data.projects.map((p) => ({ value: p.id, label: p.name }))}
      name="_f_project" id="f-project" value={data.filters.project_id}
      placeholder={t("time.field.project")}
      onselect={(v) => setFilter("project_id", v)}
    />
  </div>
  <div class="w-36">
    <DateInput name="_f_from" id="f-from" value={data.filters.date_from}
      onchange={(v) => setFilter("date_from", v)} />
  </div>
  <span class="text-xs text-neutral-400">–</span>
  <div class="w-36">
    <DateInput name="_f_to" id="f-to" value={data.filters.date_to}
      onchange={(v) => setFilter("date_to", v)} />
  </div>
  {#each statuses as status (status)}
    <button
      class="rounded-full px-3 py-1 text-xs font-medium
        {data.filters.status === status
        ? 'bg-brand text-white'
        : 'border border-neutral-300 text-neutral-600 hover:border-brand hover:text-brand'}"
      onclick={() => setFilter("status", data.filters.status === status ? "" : status)}
    >{t(`time.overview.status.${status}`)}</button>
  {/each}
</div>

<!-- Bulk actions -->
<div class="mb-3 flex flex-wrap items-center gap-2">
  <span class="text-xs text-neutral-500">
    {t("time.overview.selected", { count: selectedIds.length })}
  </span>
  {#each [
    { action: "approve", label: t("time.overview.approve") },
    { action: "unapprove", label: t("time.overview.unapprove") },
    { action: "invoice", label: t("time.overview.mark_invoiced") },
    { action: "uninvoice", label: t("time.overview.unmark_invoiced") }
  ] as bulkAction (bulkAction.action)}
    <form method="POST" action={`?/${bulkAction.action}`} use:enhance>
      <input type="hidden" name="entry_ids" value={selectedIds.join(",")} />
      <button class={bulkClass} disabled={selectedIds.length === 0}>{bulkAction.label}</button>
    </form>
  {/each}
  {#if form?.error}<span class="text-xs text-red-600">{t(form.error)}</span>{/if}
</div>

<!-- Entries table -->
<section class="overflow-hidden rounded-xl border border-neutral-200 bg-white">
  {#if entries.length === 0}
    <p class="p-8 text-center text-sm text-neutral-500">{t("time.overview.empty")}</p>
  {:else}
    <div class="overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-neutral-100 text-left text-xs text-neutral-400">
            <th class="w-8 px-3 py-2">
              <input type="checkbox" checked={allSelected} onchange={toggleAll}
                class="h-4 w-4 rounded border-neutral-300 text-brand focus:ring-brand"
                aria-label={t("time.overview.select_all")} />
            </th>
            <th class="px-2 py-2 font-medium">{t("time.field.date")}</th>
            <th class="px-2 py-2 font-medium">{t("time.overview.employee")}</th>
            <th class="px-2 py-2 font-medium">{t("time.timesheet.row")}</th>
            <th class="px-2 py-2 font-medium">{t("time.field.description")}</th>
            <th class="px-2 py-2 text-right font-medium">{t("time.field.duration")}</th>
            <th class="px-2 py-2 font-medium"></th>
          </tr>
        </thead>
        <tbody class="divide-y divide-neutral-50">
          {#each entries as e (e.id)}
            <tr class="hover:bg-neutral-50/60 {selected[e.id] ? 'bg-brand/5' : ''}">
              <td class="px-3 py-2">
                <input type="checkbox" bind:checked={selected[e.id]}
                  class="h-4 w-4 rounded border-neutral-300 text-brand focus:ring-brand"
                  aria-label={t("time.overview.select_row")} />
              </td>
              <td class="whitespace-nowrap px-2 py-2 tabular-nums text-neutral-600">
                {fmtNumericDate(e.started_at.slice(0, 10))}
                <span class="text-xs text-neutral-400">{formatTime(e.started_at)}</span>
              </td>
              <td class="whitespace-nowrap px-2 py-2 font-medium text-neutral-800">{memberName(e.user_id)}</td>
              <td class="max-w-[16rem] truncate px-2 py-2 text-neutral-700">{entryLabel(e)}</td>
              <td class="max-w-[14rem] truncate px-2 py-2 text-neutral-500">{e.description ?? ""}</td>
              <td class="px-2 py-2 text-right font-semibold tabular-nums text-neutral-900">
                {formatMinutes(e.minutes)}
                {#if !e.billable}
                  <span class="ml-1 rounded-full bg-neutral-100 px-1.5 py-0.5 text-[10px] font-medium text-neutral-500">
                    {t("time.not_billable")}
                  </span>
                {/if}
              </td>
              <td class="whitespace-nowrap px-2 py-2">
                <span class="flex items-center gap-1.5">
                  {#if e.approved_at}
                    <span title={t("time.approved")} class="text-green-600"><CircleCheck size={16} /></span>
                  {/if}
                  {#if e.invoiced_at}
                    <span title={t("time.overview.invoiced")} class="text-brand"><Receipt size={16} /></span>
                  {/if}
                </span>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</section>
