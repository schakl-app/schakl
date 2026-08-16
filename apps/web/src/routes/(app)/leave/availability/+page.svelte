<script lang="ts">
  /**
   * Beschikbaarheid — the availability rows themselves, as a list you can read, narrow and edit
   * (#368).
   *
   * The three surfaces that existed before this were all *one person's*: a section on their own
   * `/leave`, and a ⋯ modal each on the roster and Gebruikers. So "who can I book on the 14th"
   * meant opening one modal per freelancer, and last month was unreachable from any of them
   * because every host hardcoded `today → +365`.
   *
   * **No pager, and the reason is in the read, not in the screen.** `GET /leave/availability`
   * filters occurrences in Python — a repeat's dates are a cadence, not a column — so there is no
   * offset for it to page on and no column for it to sort by. The window is what bounds this
   * list instead, which is why the window is a control rather than a constant
   * (docs/PERFORMANCE.md names the exception).
   */
  import { CalendarPlus, Pencil, Plus, Trash2 } from "@lucide/svelte";

  import { page } from "$app/state";
  import { fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { memberLabel } from "$lib/core/members";
  import { can } from "$lib/core/permissions";
  import { navLabel, pageTitle } from "$lib/core/title";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import AvailabilityForm from "$lib/modules/leave/AvailabilityForm.svelte";
  import {
    availabilityKindText,
    availabilityRepeatText,
    availabilityRowId,
    availabilityRows,
    availabilityWindowText,
    type AvailabilityEntry,
    type AvailabilityRow,
  } from "$lib/modules/leave/availability";
  import { LEAVE_AVAILABILITY_COLUMNS } from "$lib/modules/leave/columns";

  let { data, form } = $props();

  /** A `DataTable` row is keyed by `id`; for a move that is the day being *added*, which is the
   *  half carrying the times and the half every control here acts on. */
  type Row = AvailabilityRow & { id: string };

  const rows = $derived(
    availabilityRows(data.rows as AvailabilityEntry[]).map((row) => ({
      ...row,
      id: row.primary.id,
    })) as Row[],
  );

  /** Mirrors the API's own key *and its scope* (§15): `:any` writes anybody's row, a plain
   *  `leave.availability.write` only the caller's own. A control that would 403 is never drawn,
   *  and the API re-checks either way. */
  const canWriteOwn = $derived(can(page.data.user, "leave.availability.write"));
  const canEdit = (row: Row): boolean =>
    data.writeAny || (canWriteOwn && row.primary.user_id === page.data.user?.id);

  let createOpen = $state(false);
  let editRow = $state<AvailabilityEntry | null>(null);
  let editOpen = $state(false);
  let deleteId = $state("");
  let deleteOpen = $state(false);

  // A calendar chip deep-links by *entry* id and this list draws a move as one line, so the id in
  // the URL is often not the id the row is keyed by (#106's shape, resolved in availability.ts).
  const highlightId = $derived(
    availabilityRowId(rows, page.url.searchParams.get("availability") ?? ""),
  );
  $effect(() => {
    if (!highlightId) return;
    document
      .getElementById(`availability-row-${highlightId}`)
      ?.scrollIntoView({ block: "center", behavior: "smooth" });
  });

  const table = createTableLayout<Row>({
    all: () => LEAVE_AVAILABILITY_COLUMNS.filter((c) => c.key !== "person" || data.anyUser),
    pref: () => data.pref,
    sort: () => null,
    cells: () => ({
      day: dayCell,
      person: personCell,
      kind: kindCell,
      window: windowCell,
      repeat: repeatCell,
      note: noteCell,
    }),
  });

  /** The window as a form the URL round-trips: submitting replaces `?from`/`?to`, so the back
   *  button lands on the span you were looking at and a link carries it (§9). */
  let from = $state(data.window.date_from);
  let to = $state(data.window.date_to);
  $effect(() => {
    from = data.window.date_from;
    to = data.window.date_to;
  });
</script>

<svelte:head
  ><title>{pageTitle(navLabel("leave", t("leave.availability.title")))}</title></svelte:head
>

<div class="mb-4 flex flex-wrap items-end justify-between gap-3">
  <div>
    <h1 class="text-lg font-semibold text-text">{t("leave.availability.title")}</h1>
    <p class="mt-0.5 text-sm text-text-muted">{t("leave.availability.overview_intro")}</p>
  </div>
  {#if canWriteOwn || data.writeAny}
    <Button onclick={() => (createOpen = true)}>
      <Plus size={16} />
      {t("leave.availability.add")}
    </Button>
  {/if}
</div>

<!-- The window and the person filter, both in the URL. A GET form rather than a click handler,
     for the same reason the pager is an <a>: this view has to be linkable. -->
<form method="GET" class="mb-4 flex flex-wrap items-end gap-3">
  <div>
    <label for="from" class="mb-1 block text-xs text-text-muted">
      {t("leave.availability.from_day")}
    </label>
    <DateInput id="from" name="from" bind:value={from} />
  </div>
  <div>
    <label for="to" class="mb-1 block text-xs text-text-muted">
      {t("leave.availability.to_day")}
    </label>
    <DateInput id="to" name="to" bind:value={to} />
  </div>
  {#if data.anyUser}
    <div class="min-w-[12rem]">
      <label for="user" class="mb-1 block text-xs text-text-muted">{t("leave.team.member")}</label>
      <Combobox
        id="user"
        name="user"
        value={data.filterUser}
        items={data.members.map((m) => ({ value: m.user_id, label: memberLabel(m) }))}
        placeholder={t("leave.availability.everyone")}
      />
    </div>
  {/if}
  <Button variant="secondary">{t("common.apply")}</Button>
</form>

<div class="mb-2 flex items-center justify-end">
  <ColumnPicker
    all={table.pickerColumns}
    visible={table.visibleKeys}
    sort={table.sort}
    onchange={table.onColumnsChange}
    onsort={table.onSort}
  />
</div>

{#if form?.error}
  <p
    class="mb-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-600 dark:bg-red-950 dark:text-red-400"
  >
    {t(form.error)}
  </p>
{/if}

<DataTable
  {rows}
  columns={table.columns}
  widths={table.widths}
  locale={data.locale}
  actions={rowActions}
  {mobileRow}
  empty={emptyState}
  onresize={table.onResize}
/>

{#snippet dayCell(row: Row)}
  <span
    id="availability-row-{row.id}"
    class="font-medium text-text {row.id === highlightId ? 'rounded bg-brand/10 px-1' : ''}"
  >
    {#if row.kind === "move"}
      <span class="line-through decoration-text-muted">{fmtNumericDate(row.from.date)}</span>
      → {fmtNumericDate(row.to.date)}
    {:else}
      {fmtNumericDate(row.entry.date)}
    {/if}
  </span>
{/snippet}

{#snippet personCell(row: Row)}
  <span class="text-text">{row.primary.user_name ?? ""}</span>
{/snippet}

{#snippet kindCell(row: Row)}
  <!-- The state carries in the words as well as the colour, so a viewer reading a printed or
       recoloured table keeps the one distinction that matters (the calendar feed's rule). -->
  <span
    class="inline-flex items-center rounded-full px-2 py-0.5 text-xs {row.kind === 'move'
      ? 'bg-surface text-text-muted'
      : row.entry.kind === 'extra'
        ? 'bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-200'
        : 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-200'}"
  >
    {availabilityKindText(row)}
  </span>
{/snippet}

{#snippet windowCell(row: Row)}
  <span class="text-text-muted">{availabilityWindowText(row.primary)}</span>
{/snippet}

{#snippet repeatCell(row: Row)}
  <span class="text-text-muted">{availabilityRepeatText(row.primary) ?? "—"}</span>
{/snippet}

{#snippet noteCell(row: Row)}
  <span class="text-text-muted">{row.primary.note ?? ""}</span>
{/snippet}

{#snippet rowActions(row: Row)}
  {#if canEdit(row)}
    <ActionsMenu
      compact
      items={[
        {
          label: t("common.edit"),
          icon: Pencil,
          onclick: () => {
            editRow = row.primary;
            editOpen = true;
          },
        },
        {
          label: t("common.delete"),
          icon: Trash2,
          danger: true,
          onclick: () => {
            deleteId = row.primary.id;
            deleteOpen = true;
          },
        },
      ]}
    />
  {/if}
{/snippet}

{#snippet mobileRow(row: Row)}
  <div class="flex items-start justify-between gap-3 px-4 py-3">
    <div class="min-w-0">
      <span class="block font-medium text-text">
        {#if row.kind === "move"}
          <span class="line-through decoration-text-muted">{fmtNumericDate(row.from.date)}</span>
          → {fmtNumericDate(row.to.date)}
        {:else}
          {fmtNumericDate(row.entry.date)}
        {/if}
      </span>
      <span class="mt-0.5 block text-xs text-text-muted">
        {#if data.anyUser && row.primary.user_name}{row.primary.user_name} ·
        {/if}
        {availabilityKindText(row)} · {availabilityWindowText(row.primary)}
        {#if availabilityRepeatText(row.primary)}· {availabilityRepeatText(row.primary)}{/if}
      </span>
    </div>
    {@render rowActions(row)}
  </div>
{/snippet}

{#snippet emptyState()}
  <div class="px-4 py-10 text-center">
    <CalendarPlus size={20} class="mx-auto mb-2 text-text-muted" />
    <p class="text-sm text-text-muted">{t("leave.availability.overview_empty")}</p>
  </div>
{/snippet}

<Modal bind:open={createOpen} title={t("leave.availability.add")}>
  <AvailabilityForm
    people={data.writeAny ? data.members : []}
    userId={data.writeAny ? "" : (page.data.user?.id ?? "")}
    error={form?.error ?? null}
    ondone={() => (createOpen = false)}
  />
</Modal>

<Modal bind:open={editOpen} title={t("leave.availability.edit")}>
  {#if editRow}
    {#key editRow.id}
      <AvailabilityForm
        entry={editRow}
        error={form?.error ?? null}
        ondone={() => (editOpen = false)}
      />
    {/key}
  {/if}
</Modal>

<ConfirmDialog
  bind:open={deleteOpen}
  title={t("common.delete")}
  message={t("leave.availability.delete_confirm")}
  action="?/deleteAvailability"
  fields={{ id: deleteId }}
  confirmLabel={t("common.delete")}
/>
