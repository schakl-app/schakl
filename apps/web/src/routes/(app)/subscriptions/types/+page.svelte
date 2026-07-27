<script lang="ts">
  /**
   * The tenant's subscription types (#142) as a full DataTable with filters and a personal
   * column layout — one tab of the subscriptions section (#229). The create/edit dialog,
   * activate/deactivate and the spawn-on-activation picker moved here from the old stacked
   * catalog.
   */
  import { Pencil, Power, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import I18nTextField from "$lib/core/ui/I18nTextField.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import SearchInput from "$lib/core/ui/SearchInput.svelte";
  import { SUBSCRIPTION_TYPE_COLUMNS } from "$lib/modules/subscriptions/columns";
  import { subscriptionTypeLabel } from "$lib/modules/subscriptions/types";

  let { data, form } = $props();

  type SubscriptionType = (typeof data.types)[number];

  const STATUS_FILTERS = ["active", "inactive"] as const;

  const busy = new InFlight();
  let showModal = $state(false);
  let editing = $state<SubscriptionType | null>(null);
  let deleteId = $state("");
  let confirmDelete = $state(false);
  let toggleForm: HTMLFormElement | undefined = $state();
  let toggleId = $state("");
  let toggleActive = $state("");
  // The task templates this type spawns on first activation (#142) — chips + type-ahead.
  let spawnTemplates = $state<{ id: string; name: string }[]>([]);
  const spawnJson = $derived(JSON.stringify(spawnTemplates.map((tpl) => tpl.id)));
  const taskTemplateItems = $derived(
    data.taskTemplates
      .filter((tpl) => !spawnTemplates.some((s) => s.id === tpl.id))
      .map((tpl) => ({ value: tpl.id, label: tpl.name })),
  );

  function taskTemplateName(id: string): string {
    return data.taskTemplates.find((tpl) => tpl.id === id)?.name ?? "—";
  }
  function openCreate() {
    editing = null;
    spawnTemplates = [];
    showModal = true;
  }
  function openEdit(st: SubscriptionType) {
    editing = st;
    spawnTemplates = (st.task_template_ids ?? []).map((id) => ({
      id,
      name: taskTemplateName(id),
    }));
    showModal = true;
  }
  function requestToggle(st: SubscriptionType) {
    toggleId = st.id;
    toggleActive = String(!st.active);
    // Post after the hidden fields re-render with this row's values — a synchronous
    // requestSubmit reads the previous state (same trick as the list's saveAsTemplate).
    setTimeout(() => toggleForm?.requestSubmit(), 0);
  }

  function setFilter(key: string, value: string) {
    const url = new URL(page.url);
    if (value) url.searchParams.set(key, value);
    else url.searchParams.delete(key);
    void goto(url, { keepFocus: true, noScroll: true });
  }

  const table = createTableLayout<SubscriptionType>({
    all: () => SUBSCRIPTION_TYPE_COLUMNS,
    pref: () => data.table.pref,
    sort: () => data.table.sort,
    cells: () => ({
      label: labelCell,
      key: keyCell,
      tasks: tasksCell,
      active: activeCell,
    }),
  });
</script>

<svelte:head>
  <title>{pageTitle(t("settings.subscriptions.types_heading"))}</title>
</svelte:head>

<div class="mb-6 flex flex-wrap items-center justify-between gap-3">
  <div>
    <h1 class="text-xl font-semibold text-text">{t("settings.subscriptions.types_heading")}</h1>
    <p class="mt-1 text-sm text-text-muted">{t("settings.subscriptions.types_subtitle")}</p>
  </div>
  <button
    class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
    onclick={openCreate}>{t("settings.subscriptions.new_type")}</button
  >
</div>

<div class="mb-4 flex flex-wrap items-center gap-2">
  <SearchInput />
  {#each STATUS_FILTERS as status (status)}
    <button
      class="rounded-full px-3 py-1 text-xs font-medium
        {data.statusFilter === status
        ? 'bg-brand/10 text-brand ring-2 ring-brand'
        : 'bg-surface text-text-muted hover:text-text'}"
      aria-pressed={data.statusFilter === status}
      onclick={() => setFilter("status", data.statusFilter === status ? "" : status)}
      >{t(`settings.subscriptions.${status}`)}</button
    >
  {/each}
  {#if data.statusFilter || data.q}
    <button
      class="text-xs text-text-muted underline hover:text-text"
      onclick={() => {
        const url = new URL(page.url);
        url.searchParams.delete("status");
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

{#snippet labelCell(st: SubscriptionType)}
  <button
    type="button"
    class="text-left font-medium {st.active ? 'text-text' : 'text-text-muted'} hover:text-brand"
    onclick={() => openEdit(st)}>{subscriptionTypeLabel(st, data.locale)}</button
  >
{/snippet}

{#snippet keyCell(st: SubscriptionType)}
  <span class="text-text-muted">{st.key}</span>
{/snippet}

{#snippet tasksCell(st: SubscriptionType)}
  <span class="tabular-nums text-text-muted">{(st.task_template_ids ?? []).length || "—"}</span>
{/snippet}

{#snippet activeCell(st: SubscriptionType)}
  <span class="rounded-md bg-surface px-2 py-0.5 text-xs text-text-muted"
    >{st.active ? t("settings.subscriptions.active") : t("settings.subscriptions.inactive")}</span
  >
{/snippet}

{#snippet rowActions(st: SubscriptionType)}
  <ActionsMenu
    compact
    items={[
      { label: t("common.edit"), icon: Pencil, onclick: () => openEdit(st) },
      {
        label: st.active ? t("common.deactivate") : t("common.activate"),
        icon: Power,
        onclick: () => requestToggle(st),
      },
      {
        label: t("common.delete"),
        icon: Trash2,
        danger: true,
        onclick: () => {
          deleteId = st.id;
          confirmDelete = true;
        },
      },
    ]}
  />
{/snippet}

{#snippet mobileRow(st: SubscriptionType)}
  <button type="button" class="min-w-0 flex-1 text-left" onclick={() => openEdit(st)}>
    <span class="block truncate text-sm font-medium {st.active ? 'text-text' : 'text-text-muted'}"
      >{subscriptionTypeLabel(st, data.locale)}</span
    >
    <span class="mt-0.5 block truncate text-xs text-text-muted">
      {st.key}{st.active ? "" : ` · ${t("settings.subscriptions.inactive")}`}
    </span>
  </button>
{/snippet}

{#snippet emptyState()}
  <p class="p-6 text-sm text-text-muted">{t("settings.subscriptions.types_empty")}</p>
{/snippet}

<DataTable
  rows={data.types}
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
  title={editing ? t("settings.subscriptions.edit_type") : t("settings.subscriptions.new_type")}
>
  {#key editing?.id ?? "new"}
    <form
      method="POST"
      action="?/saveType"
      class="space-y-4"
      use:enhance={busy.wrap("", () => ({ result, update }) => {
        if (result.type === "success") showModal = false;
        void update({ reset: false });
      })}
    >
      {#if editing}<input type="hidden" name="id" value={editing.id} />{/if}
      {#key editing?.id ?? "new"}
        <I18nTextField
          label={t("common.label_field")}
          basename="label"
          values={editing?.label_i18n ?? {}}
          idPrefix="st"
        />
      {/key}
      <div>
        <span class="mb-1 block text-sm text-text"
          >{t("settings.subscriptions.task_templates")}</span
        >
        {#if spawnTemplates.length > 0}
          <div class="mb-2 flex flex-wrap gap-1.5">
            {#each spawnTemplates as tpl (tpl.id)}
              <span
                class="inline-flex items-center gap-1 rounded-full border border-border bg-surface px-2.5 py-0.5 text-xs text-text"
              >
                {tpl.name}
                <button
                  type="button"
                  class="text-text-muted hover:text-red-600 dark:hover:text-red-400"
                  aria-label={t("common.delete")}
                  onclick={() => (spawnTemplates = spawnTemplates.filter((s) => s.id !== tpl.id))}
                  >✕</button
                >
              </span>
            {/each}
          </div>
        {/if}
        <Combobox
          items={taskTemplateItems}
          name="task_template_picker"
          id="st-task-templates"
          placeholder={t("settings.subscriptions.task_templates")}
          onselect={(value) => {
            if (value && !spawnTemplates.some((s) => s.id === value)) {
              spawnTemplates = [...spawnTemplates, { id: value, name: taskTemplateName(value) }];
            }
          }}
        />
        <input type="hidden" name="task_template_ids" value={spawnJson} />
        <p class="mt-1 text-xs text-text-muted">
          {t("settings.subscriptions.task_templates_help")}
        </p>
      </div>
      <input
        type="hidden"
        name="position"
        value={editing?.position ?? data.types.length * 10 + 10}
      />
      {#if form?.error}<p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>{/if}
      <div class="flex justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm text-text"
          onclick={() => (showModal = false)}>{t("common.cancel")}</button
        >
        <Button loading={busy.active}>{t("common.save")}</Button>
      </div>
    </form>
  {/key}
</Modal>

<form bind:this={toggleForm} method="POST" action="?/toggleType" use:enhance class="hidden">
  <input type="hidden" name="id" value={toggleId} />
  <input type="hidden" name="active" value={toggleActive} />
</form>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("settings.subscriptions.delete_type")}
  message={t("settings.subscriptions.delete_type_confirm")}
  action="?/deleteType"
  fields={{ id: deleteId }}
/>
