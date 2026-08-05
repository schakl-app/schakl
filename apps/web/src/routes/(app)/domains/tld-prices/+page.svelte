<script lang="ts">
  /**
   * The TLD price list (#250) — one tab of the domains section (#229's catalog-as-tab
   * pattern). Each row is a TLD: the price in effect, anything scheduled, and how many
   * domains ride it. Prices are append-only history: setting one appends (same-day
   * corrects in place), the bulk increase is the #231 preview-then-apply modal, and a
   * scheduled row can be deleted before its day comes.
   */
  import { Pencil, Trash2, TrendingUp } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { fmtMoney, fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import ImpexBar from "$lib/core/impex/ImpexBar.svelte";
  import { can } from "$lib/core/permissions";
  import { InFlight } from "$lib/core/submit.svelte";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import { pageTitle } from "$lib/core/title";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import { TLD_PRICE_COLUMNS } from "$lib/modules/domains/columns";
  import TldPriceIncreaseModal from "$lib/modules/domains/TldPriceIncreaseModal.svelte";

  let { data, form } = $props();

  // DataTable keys rows by `id`; a TLD is its own natural key.
  const rows = $derived(data.groups.map((group) => ({ ...group, id: group.tld })));
  type Group = (typeof rows)[number];

  const busy = new InFlight();
  const canManage = $derived(can(page.data.user, "domains.tld_price.manage"));

  // Set-price dialog: opened blank from the toolbar, or prefilled from a row's ⋯.
  let showPrice = $state(false);
  let priceTld = $state("");
  function openSetPrice(tld = "") {
    priceTld = tld;
    showPrice = true;
  }

  // Price-increase modal: toolbar = free scope, row ⋮ = locked to that TLD.
  let increaseOpen = $state(false);
  let increaseScope = $state("all");
  let increaseLocked = $state(false);
  function openIncrease(scope: string, locked: boolean) {
    increaseScope = scope;
    increaseLocked = locked;
    increaseOpen = true;
  }
  const scopeItems = $derived([
    { value: "all", label: t("domains.price_increase.scope_all") },
    ...data.groups
      .filter((g) => g.current || (g.upcoming ?? []).length > 0)
      .map((g) => ({ value: `tld:${g.tld}`, label: `.${g.tld}` })),
  ]);

  let deleteRowId = $state("");
  let confirmDelete = $state(false);
  function requestDeleteRow(id: string) {
    deleteRowId = id;
    confirmDelete = true;
  }

  const money = (value: string | number | null | undefined) =>
    value == null ? "—" : fmtMoney(Number(value));

  function rowMenuItems(group: Group) {
    return [
      { label: t("domains.tld_prices.edit"), icon: Pencil, onclick: () => openSetPrice(group.tld) },
      ...(group.current
        ? [
            {
              label: t("domains.price_increase.row_action"),
              icon: TrendingUp,
              onclick: () => openIncrease(`tld:${group.tld}`, true),
            },
          ]
        : []),
      // A scheduled row can be undone before its day comes; each names its date.
      ...(group.upcoming ?? []).map((row) => ({
        label: `${t("domains.tld_prices.delete_row")}: ${fmtNumericDate(row.valid_from)}`,
        icon: Trash2,
        danger: true,
        onclick: () => requestDeleteRow(row.id),
      })),
    ];
  }

  const table = createTableLayout<Group>({
    all: () => TLD_PRICE_COLUMNS,
    pref: () => data.table.pref,
    sort: () => null,
    cells: () => ({
      tld: tldCell,
      current: currentCell,
      since: sinceCell,
      upcoming: upcomingCell,
      domains: domainsCell,
    }),
  });

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand";
</script>

{#snippet tldCell(group: Group)}
  <span class="font-medium text-text">.{group.tld}</span>
{/snippet}

{#snippet currentCell(group: Group)}
  {#if group.current}
    <span class="tabular-nums text-text">{money(group.current.amount)}</span>
  {:else}
    <span class="text-text-muted">{t("domains.tld_prices.no_price")}</span>
  {/if}
{/snippet}

{#snippet sinceCell(group: Group)}
  <span class="tabular-nums text-text-muted">
    {group.current ? fmtNumericDate(group.current.valid_from) : "—"}
  </span>
{/snippet}

{#snippet upcomingCell(group: Group)}
  {#if (group.upcoming ?? []).length > 0}
    <span class="text-text-muted">
      {(group.upcoming ?? [])
        .map((row) =>
          t("domains.tld_prices.upcoming_on", {
            amount: money(row.amount),
            date: fmtNumericDate(row.valid_from),
          }),
        )
        .join(" · ")}
    </span>
  {:else}
    <span class="text-text-muted">—</span>
  {/if}
{/snippet}

{#snippet domainsCell(group: Group)}
  <span class="tabular-nums text-text-muted">{group.domain_count}</span>
{/snippet}

{#snippet rowActions(group: Group)}
  <ActionsMenu items={rowMenuItems(group)} />
{/snippet}

{#snippet mobileRow(group: Group)}
  <div class="flex items-center gap-3">
    <div class="min-w-0 flex-1">
      <span class="font-medium text-text">.{group.tld}</span>
      <span class="mt-0.5 block truncate text-sm text-text-muted">
        {group.current ? money(group.current.amount) : t("domains.tld_prices.no_price")}
        · {t("domains.tld_prices.domains")}: {group.domain_count}
      </span>
    </div>
    {#if canManage}
      {@render rowActions(group)}
    {/if}
  </div>
{/snippet}

{#snippet emptyState()}
  <div class="rounded-xl border border-dashed border-border bg-surface-raised p-10 text-center">
    <p class="font-medium text-text">{t("domains.tld_prices.empty")}</p>
  </div>
{/snippet}

<svelte:head>
  <title>{pageTitle(t("domains.tld_prices.title"))}</title>
</svelte:head>

<div class="mb-6 flex flex-wrap items-center justify-between gap-3">
  <div>
    <h1 class="text-xl font-semibold text-text">{t("domains.tld_prices.title")}</h1>
    <p class="mt-1 text-sm text-text-muted">{t("domains.tld_prices.subtitle")}</p>
  </div>
  {#if canManage}
    <div class="flex items-center gap-2">
      <button
        class="rounded-lg border border-border px-4 py-2 text-sm font-medium text-text"
        onclick={() => openIncrease("all", false)}>{t("domains.price_increase.title")}</button
      >
      <button
        class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white"
        onclick={() => openSetPrice()}>{t("domains.tld_prices.add")}</button
      >
    </div>
  {/if}
</div>

{#if form?.priceApplied != null}
  <p class="mb-4 rounded-lg border border-border bg-surface-raised px-4 py-2 text-sm text-text">
    {t("domains.price_increase.applied", { count: form.priceApplied })}
  </p>
{/if}

<div class="mb-4 flex flex-wrap items-center justify-end gap-2">
  <ImpexBar
    entity="domain_tld_price"
    readPermission="domains.tld_price.read"
    writePermission="domains.tld_price.manage"
    locale={data.locale}
    {form}
  />
  <ColumnPicker
    all={table.pickerColumns}
    visible={table.visibleKeys}
    sort={table.sort}
    onchange={table.onColumnsChange}
    onsort={table.onSort}
  />
</div>

<DataTable
  {rows}
  columns={table.columns}
  sort={table.sort}
  widths={table.widths}
  locale={data.locale}
  actions={canManage ? rowActions : undefined}
  {mobileRow}
  empty={emptyState}
  onsort={table.onSort}
  onresize={table.onResize}
/>

{#if canManage}
  <Modal bind:open={showPrice} title={t("domains.tld_prices.add")}>
    <form
      method="POST"
      action="?/savePrice"
      use:enhance={busy.wrap("save", () => ({ result, update }) => {
        if (result.type === "success") showPrice = false;
        void update({ reset: false });
      })}
      class="space-y-4"
    >
      <div>
        <label for="price-tld" class="mb-1 block text-sm text-text"
          >{t("domains.tld_prices.tld")}</label
        >
        <input
          id="price-tld"
          name="tld"
          required
          bind:value={priceTld}
          placeholder="nl"
          class={inputClass}
        />
      </div>
      <div>
        <label for="price-amount" class="mb-1 block text-sm text-text"
          >{t("domains.tld_prices.amount")}</label
        >
        <input
          id="price-amount"
          name="amount"
          type="number"
          step="0.01"
          min="0"
          required
          class={inputClass}
        />
      </div>
      <div>
        <label for="price-from" class="mb-1 block text-sm text-text"
          >{t("domains.tld_prices.valid_from")}</label
        >
        <DateInput name="valid_from" id="price-from" value="" />
        <p class="mt-1 text-xs text-text-muted">{t("domains.tld_prices.valid_from_hint")}</p>
      </div>
      {#if form?.priceError}
        <p class="text-sm text-red-600 dark:text-red-400">{t(form.priceError)}</p>
      {/if}
      <div class="flex justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm text-text"
          onclick={() => (showPrice = false)}>{t("common.cancel")}</button
        >
        <Button loading={busy.is("save")} disabled={busy.active}>{t("common.save")}</Button>
      </div>
    </form>
  </Modal>

  <TldPriceIncreaseModal
    bind:open={increaseOpen}
    bind:scope={increaseScope}
    locked={increaseLocked}
    {scopeItems}
    {form}
  />

  <ConfirmDialog
    bind:open={confirmDelete}
    title={t("domains.tld_prices.delete_row")}
    message={t("domains.tld_prices.delete_row_confirm")}
    action="?/deletePrice"
    fields={{ id: deleteRowId }}
  />
{/if}
