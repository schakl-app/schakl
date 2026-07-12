<script lang="ts">
  import { Pencil, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { fmtMoney, fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import CustomFieldsForm from "$lib/core/customfields/CustomFieldsForm.svelte";
  import CompanyQuickCreate from "$lib/modules/companies/CompanyQuickCreate.svelte";

  let { data, form } = $props();

  type Subscription = (typeof data.subscriptions)[number];

  let showForm = $state(false);
  let editing = $state<Subscription | null>(null);
  let deleteId = $state("");
  let confirmDelete = $state(false);

  // Inline company create from the picker (#115, docs/UX.md — per-picker definition of done).
  let qcCompanyOpen = $state(false);
  let qcCompanyName = $state("");
  let createdCompanyId = $state("");
  $effect(() => {
    const created = form?.inlineCreated;
    if (created?.slot === "company") createdCompanyId = created.id;
  });

  const companyItems = $derived(data.companies.map((c) => ({ value: c.id, label: c.name })));
  const STATUSES = ["draft", "active", "paused", "cancelled"] as const;
  const INTERVALS = ["monthly", "quarterly", "yearly"] as const;

  function openCreate() {
    editing = null;
    createdCompanyId = "";
    showForm = true;
  }
  function openEdit(sub: Subscription) {
    editing = sub;
    createdCompanyId = "";
    showForm = true;
  }

  const money = (value: string | number | null | undefined) =>
    value == null ? "—" : fmtMoney(Number(value));

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<svelte:head>
  <title>{pageTitle(t("subscriptions.title"))}</title>
</svelte:head>

<div class="mb-6 flex flex-wrap items-center justify-between gap-3">
  <h1 class="text-xl font-semibold text-text">{t("subscriptions.title")}</h1>
  <button
    class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
    onclick={openCreate}>{t("subscriptions.add")}</button
  >
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

<section class="rounded-xl border border-border bg-surface-raised">
  {#if data.subscriptions.length === 0}
    <p class="p-6 text-sm text-text-muted">{t("subscriptions.empty")}</p>
  {:else}
    <div class="overflow-x-auto">
      <table class="w-full text-sm">
        <thead class="border-b border-border text-left text-xs uppercase text-text-muted">
          <tr>
            <th class="px-4 py-2 font-medium">{t("subscriptions.field.name")}</th>
            <th class="px-4 py-2 font-medium">{t("subscriptions.field.company")}</th>
            <th class="px-4 py-2 font-medium">{t("subscriptions.field.amount")}</th>
            <th class="px-4 py-2 font-medium">{t("subscriptions.field.next_invoice")}</th>
            <th class="px-4 py-2 font-medium">{t("subscriptions.field.status")}</th>
            <th class="px-4 py-2"></th>
          </tr>
        </thead>
        <tbody class="divide-y divide-border">
          {#each data.subscriptions as sub (sub.id)}
            <tr class="hover:bg-surface">
              <td class="px-4 py-2 font-medium text-text">{sub.name}</td>
              <td class="px-4 py-2 text-text-muted">{sub.company_name}</td>
              <td class="px-4 py-2 tabular-nums text-text">
                {money(sub.amount)}
                <span class="text-xs text-text-muted">
                  · {t(`subscriptions.interval.${sub.interval}`)}</span
                >
              </td>
              <td class="px-4 py-2 tabular-nums text-text-muted">
                {sub.next_invoice_date ? fmtNumericDate(sub.next_invoice_date) : "—"}
              </td>
              <td class="px-4 py-2">
                <span class="rounded-md bg-surface px-2 py-0.5 text-xs text-text-muted"
                  >{t(`subscriptions.status.${sub.status}`)}</span
                >
              </td>
              <td class="px-2 py-2 text-right">
                <ActionsMenu
                  compact
                  items={[
                    { label: t("common.edit"), icon: Pencil, onclick: () => openEdit(sub) },
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
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</section>

<!-- One form for create and edit (use vs edit mode: definition changes live here). -->
<Modal
  bind:open={showForm}
  title={editing ? t("common.edit") : t("subscriptions.add")}
>
  {#key editing?.id ?? "new"}
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
        <input id="sub-name" name="name" required value={editing?.name ?? ""} class={inputClass} />
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
      <div class="grid gap-3 sm:grid-cols-2">
        <div>
          <label for="sub-status" class="mb-1 block text-sm font-medium text-text"
            >{t("subscriptions.field.status")}</label
          >
          <select id="sub-status" name="status" class={inputClass}>
            {#each STATUSES as status (status)}
              <option value={status} selected={(editing?.status ?? "draft") === status}
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
              <option value={interval} selected={(editing?.interval ?? "monthly") === interval}
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
            value={editing?.amount ?? ""}
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
            value={editing?.included_hours ?? ""}
            class={inputClass}
          />
        </div>
        <div>
          <label for="sub-start" class="mb-1 block text-sm font-medium text-text"
            >{t("subscriptions.field.start_date")}</label
          >
          <DateInput name="start_date" id="sub-start" required value={editing?.start_date ?? ""} />
        </div>
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
      </div>
      <div>
        <label for="sub-notes" class="mb-1 block text-sm font-medium text-text"
          >{t("subscriptions.field.notes")}</label
        >
        <textarea id="sub-notes" name="notes" rows="2" class={inputClass}
          >{editing?.notes ?? ""}</textarea
        >
      </div>
      {#if data.definitions.length > 0}
        <CustomFieldsForm
          definitions={data.definitions}
          values={editing?.custom ?? {}}
          locale={data.locale}
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

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("subscriptions.delete")}
  message={t("subscriptions.delete_confirm")}
  action="?/delete"
  fields={{ id: deleteId }}
/>
