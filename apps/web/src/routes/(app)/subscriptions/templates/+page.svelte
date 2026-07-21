<script lang="ts">
  /**
   * The standard subscriptions (the `subscription_templates` presets, renamed in the UI only,
   * #229): a full DataTable with filters and a personal column layout, one tab of the
   * subscriptions section. The create/edit dialog moved here from the old stacked catalog.
   */
  import { Pencil, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import { fmtMoney } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import SearchInput from "$lib/core/ui/SearchInput.svelte";
  import { SUBSCRIPTION_TEMPLATE_COLUMNS } from "$lib/modules/subscriptions/columns";
  import { subscriptionTypeLabel } from "$lib/modules/subscriptions/types";

  let { data, form } = $props();

  type Template = (typeof data.templates)[number];

  const INTERVALS = ["monthly", "quarterly", "yearly"] as const;

  let showModal = $state(false);
  let editing = $state<Template | null>(null);
  let deleteId = $state("");
  let confirmDelete = $state(false);

  const activeTypes = $derived(data.types.filter((st) => st.active));
  const typeLabel = (id: string | null | undefined) =>
    subscriptionTypeLabel(
      data.types.find((st) => st.id === id),
      data.locale,
    );
  const money = (value: string | null | undefined) =>
    value == null ? "—" : fmtMoney(Number(value));

  function openCreate() {
    editing = null;
    showModal = true;
  }
  function openEdit(tpl: Template) {
    editing = tpl;
    showModal = true;
  }

  function setFilter(key: string, value: string) {
    const url = new URL(page.url);
    if (value) url.searchParams.set(key, value);
    else url.searchParams.delete(key);
    void goto(url, { keepFocus: true, noScroll: true });
  }

  const table = createTableLayout<Template>({
    all: () => SUBSCRIPTION_TEMPLATE_COLUMNS,
    pref: () => data.table.pref,
    sort: () => data.table.sort,
    cells: () => ({
      name: nameCell,
      type: typeCell,
      interval: intervalCell,
      amount: amountCell,
      included_hours: includedCell,
      notice_period_days: noticeCell,
      notes: notesCell,
    }),
  });

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand";
</script>

<svelte:head>
  <title>{pageTitle(t("settings.subscriptions.templates_heading"))}</title>
</svelte:head>

<div class="mb-6 flex flex-wrap items-center justify-between gap-3">
  <div>
    <h1 class="text-xl font-semibold text-text">
      {t("settings.subscriptions.templates_heading")}
    </h1>
    <p class="mt-1 text-sm text-text-muted">{t("settings.subscriptions.templates_subtitle")}</p>
  </div>
  <button
    class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
    onclick={openCreate}>{t("settings.subscriptions.new_template")}</button
  >
</div>

<div class="mb-4 flex flex-wrap items-center gap-2">
  <SearchInput />
  {#each activeTypes as st (st.id)}
    <button
      class="rounded-full px-3 py-1 text-xs font-medium
        {data.typeFilter === st.id
        ? 'bg-brand/10 text-brand ring-2 ring-brand'
        : 'bg-surface text-text-muted hover:text-text'}"
      aria-pressed={data.typeFilter === st.id}
      onclick={() => setFilter("type", data.typeFilter === st.id ? "" : st.id)}
      >{subscriptionTypeLabel(st, data.locale)}</button
    >
  {/each}
  {#if data.typeFilter || data.q}
    <button
      class="text-xs text-text-muted underline hover:text-text"
      onclick={() => {
        const url = new URL(page.url);
        url.searchParams.delete("type");
        url.searchParams.delete("q");
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

{#snippet nameCell(tpl: Template)}
  <button
    type="button"
    class="text-left font-medium text-text hover:text-brand"
    onclick={() => openEdit(tpl)}>{tpl.name}</button
  >
{/snippet}

{#snippet typeCell(tpl: Template)}
  <span class="text-text-muted"
    >{tpl.subscription_type_id ? typeLabel(tpl.subscription_type_id) : "—"}</span
  >
{/snippet}

{#snippet intervalCell(tpl: Template)}
  <span class="text-text-muted">{t(`subscriptions.interval.${tpl.interval}`)}</span>
{/snippet}

{#snippet amountCell(tpl: Template)}
  <span class="tabular-nums text-text">{money(tpl.amount)}</span>
{/snippet}

{#snippet includedCell(tpl: Template)}
  <span class="tabular-nums text-text-muted">{tpl.included_hours ?? "—"}</span>
{/snippet}

{#snippet noticeCell(tpl: Template)}
  <span class="tabular-nums text-text-muted">{tpl.notice_period_days ?? "—"}</span>
{/snippet}

{#snippet notesCell(tpl: Template)}
  <span class="block max-w-64 truncate text-text-muted">{tpl.notes ?? "—"}</span>
{/snippet}

{#snippet rowActions(tpl: Template)}
  <ActionsMenu
    compact
    items={[
      { label: t("common.edit"), icon: Pencil, onclick: () => openEdit(tpl) },
      {
        label: t("common.delete"),
        icon: Trash2,
        danger: true,
        onclick: () => {
          deleteId = tpl.id;
          confirmDelete = true;
        },
      },
    ]}
  />
{/snippet}

{#snippet mobileRow(tpl: Template)}
  <button type="button" class="min-w-0 flex-1 text-left" onclick={() => openEdit(tpl)}>
    <span class="block truncate text-sm font-medium text-text">{tpl.name}</span>
    <span class="mt-0.5 block truncate text-xs text-text-muted">
      {money(tpl.amount)} · {t(`subscriptions.interval.${tpl.interval}`)}
    </span>
  </button>
{/snippet}

{#snippet emptyState()}
  <p class="p-6 text-sm text-text-muted">{t("settings.subscriptions.templates_empty")}</p>
{/snippet}

<DataTable
  rows={data.templates}
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

<!-- Create/edit — the same single-save dialog the old stacked catalog had. -->
<Modal
  bind:open={showModal}
  title={editing
    ? t("settings.subscriptions.edit_template")
    : t("settings.subscriptions.new_template")}
>
  {#key editing?.id ?? "new"}
    <form
      method="POST"
      action="?/saveTemplate"
      class="space-y-4"
      use:enhance={() =>
        ({ result, update }) => {
          if (result.type === "success") showModal = false;
          void update({ reset: false });
        }}
    >
      {#if editing}<input type="hidden" name="id" value={editing.id} />{/if}
      <div>
        <label for="tpl-name" class="mb-1 block text-sm text-text"
          >{t("subscriptions.field.name")}</label
        >
        <input id="tpl-name" name="name" required value={editing?.name ?? ""} class={inputClass} />
      </div>
      <div class="grid gap-3 sm:grid-cols-2">
        <div>
          <label for="tpl-type" class="mb-1 block text-sm text-text"
            >{t("subscriptions.field.type")}</label
          >
          <select id="tpl-type" name="subscription_type_id" class={inputClass}>
            <option value="">—</option>
            {#each activeTypes as st (st.id)}
              <option value={st.id} selected={editing?.subscription_type_id === st.id}
                >{subscriptionTypeLabel(st, data.locale)}</option
              >
            {/each}
          </select>
        </div>
        <div>
          <label for="tpl-interval" class="mb-1 block text-sm text-text"
            >{t("subscriptions.field.interval")}</label
          >
          <select id="tpl-interval" name="interval" class={inputClass}>
            {#each INTERVALS as interval (interval)}
              <option value={interval} selected={(editing?.interval ?? "monthly") === interval}
                >{t(`subscriptions.interval.${interval}`)}</option
              >
            {/each}
          </select>
        </div>
        <div>
          <label for="tpl-amount" class="mb-1 block text-sm text-text"
            >{t("subscriptions.field.amount")}</label
          >
          <input
            id="tpl-amount"
            name="amount"
            type="number"
            min="0"
            step="0.01"
            value={editing?.amount ?? ""}
            class={inputClass}
          />
        </div>
        <div>
          <label for="tpl-included" class="mb-1 block text-sm text-text"
            >{t("subscriptions.field.included_hours")}</label
          >
          <input
            id="tpl-included"
            name="included_hours"
            type="number"
            min="0"
            step="0.5"
            value={editing?.included_hours ?? ""}
            class={inputClass}
          />
        </div>
        <div>
          <label for="tpl-notice" class="mb-1 block text-sm text-text"
            >{t("subscriptions.field.notice_period_days")}</label
          >
          <input
            id="tpl-notice"
            name="notice_period_days"
            type="number"
            min="0"
            max="365"
            value={editing?.notice_period_days ?? ""}
            class={inputClass}
          />
        </div>
      </div>
      <div>
        <label for="tpl-notes" class="mb-1 block text-sm text-text"
          >{t("subscriptions.field.notes")}</label
        >
        <textarea id="tpl-notes" name="notes" rows="2" class={inputClass}
          >{editing?.notes ?? ""}</textarea
        >
      </div>
      <input
        type="hidden"
        name="position"
        value={editing?.position ?? data.templates.length * 10 + 10}
      />
      {#if form?.error}<p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>{/if}
      <div class="flex justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm text-text"
          onclick={() => (showModal = false)}>{t("common.cancel")}</button
        >
        <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white"
          >{t("common.save")}</button
        >
      </div>
    </form>
  {/key}
</Modal>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("settings.subscriptions.delete_template")}
  message={t("settings.subscriptions.delete_template_confirm")}
  action="?/deleteTemplate"
  fields={{ id: deleteId }}
/>
