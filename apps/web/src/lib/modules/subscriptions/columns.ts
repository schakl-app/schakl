/**
 * The columns the subscriptions list can show (#153, the shared DataTable — #24).
 *
 * Plain metadata, no Svelte. `sortKey` mirrors the API's allow-list
 * (`apps/api/app/modules/subscriptions/service.py::SORTABLE`), which covers every column:
 * company sorts by the name the cell prints, type by the tenant's declared position, interval by
 * period length, amount by the current price — all server-side, because the list is paginated.
 *
 * Every column but the `primary` one declares a `width`, because the table is `table-fixed`
 * (docs/UX.md): the one column without a width absorbs the slack, and columns that all leave it
 * out share the remainder equally. The widths are sized to what the cells actually print — a
 * money or date cell needs far less room than a client name — and the default-visible set adds
 * up to well under a laptop's content box, so the grid never scrolls sideways.
 */
import type { ColumnMeta } from "$lib/core/table/columns";

export const SUBSCRIPTIONS_TABLE_ID = "subscriptions";

export const SUBSCRIPTION_COLUMNS: ColumnMeta[] = [
  // The client leads, and is the primary column: the list is sectioned by standard
  // subscription, so inside a section the agreement's name is the heading repeated and the one
  // thing that distinguishes a row is *whose* it is. The widest column too — a client name is
  // the only free-form text a row prints.
  {
    key: "company",
    labelKey: "subscriptions.field.company",
    sortKey: "company",
    primary: true,
    width: 220,
  },
  // Still on by default: an agreement that came from no standard subscription has only its
  // name to say what it is, and inside a section the name is what says a row *stopped*
  // following its preset (docs/UX.md: the grouped-by column loses its sort, not its place).
  // No `sortKey`, because a sort orders rows within a section and the sections are the names.
  { key: "name", labelKey: "subscriptions.field.name", defaultVisible: true, width: 200 },
  // Tenant-defined category labels (#142) — short by convention, but the tenant writes them.
  {
    key: "type",
    labelKey: "subscriptions.field.type",
    sortKey: "type",
    defaultVisible: true,
    width: 120,
  },
  // A closed vocabulary of three: "Maandelijks" is the longest thing this can ever print.
  {
    key: "interval",
    labelKey: "subscriptions.field.interval",
    sortKey: "interval",
    defaultVisible: true,
    // 100 counted the longest label and forgot the cell's own `px-4`: "Per kwartaal" is 82px in
    // a 68px content box, so the most ordinary value on the list read as "Per kwart…".
    width: 120,
  },
  {
    key: "amount",
    labelKey: "subscriptions.field.amount",
    sortKey: "amount",
    align: "right",
    defaultVisible: true,
    width: 100,
  },
  // A date, but the header ("Volgende factuurdatum") is longer than any value under it.
  {
    key: "next_invoice",
    labelKey: "subscriptions.field.next_invoice",
    sortKey: "next_invoice_date",
    align: "right",
    defaultVisible: true,
    width: 110,
  },
  {
    key: "status",
    labelKey: "subscriptions.field.status",
    sortKey: "status",
    defaultVisible: true,
    // Same arithmetic as `interval`: a "Gepauzeerd" pill is 84px before the cell's padding.
    width: 120,
  },
  {
    key: "start_date",
    labelKey: "subscriptions.field.start_date",
    sortKey: "start_date",
    width: 110,
  },
  {
    key: "included_hours",
    labelKey: "subscriptions.field.included_hours",
    sortKey: "included_hours",
    align: "right",
    width: 120,
  },
  // Off by default, but reachable: this is the one surface that shows a note with its
  // variables resolved (#259) — without the column the cell that does that was unreachable.
  // Wide because it is prose, and matched to the cell's own `max-w-64` truncation.
  { key: "notes", labelKey: "subscriptions.field.notes", width: 260 },
];

/**
 * The catalog tables (#229). Unlike the list above, their `sortKey`s are honoured by the
 * page's own `load` — the catalog is small and fetched whole, so the server-side sort the
 * DataTable contract expects happens there, not in the API.
 */
export const SUBSCRIPTION_TEMPLATES_TABLE_ID = "subscription_templates";

export const SUBSCRIPTION_TEMPLATE_COLUMNS: ColumnMeta[] = [
  {
    key: "name",
    labelKey: "subscriptions.field.name",
    sortKey: "name",
    primary: true,
    width: 220,
  },
  {
    key: "type",
    labelKey: "subscriptions.field.type",
    sortKey: "type",
    defaultVisible: true,
    width: 150,
  },
  {
    key: "interval",
    labelKey: "subscriptions.field.interval",
    sortKey: "interval",
    defaultVisible: true,
    width: 120,
  },
  {
    key: "amount",
    labelKey: "subscriptions.field.amount",
    sortKey: "amount",
    align: "right",
    defaultVisible: true,
    width: 110,
  },
  {
    key: "included_hours",
    labelKey: "subscriptions.field.included_hours",
    sortKey: "included_hours",
    align: "right",
    defaultVisible: true,
    width: 130,
  },
  // Header-bound again: "Opzegtermijn (dagen)" is far wider than the number beneath it.
  {
    key: "notice_period_days",
    labelKey: "subscriptions.field.notice_period_days",
    sortKey: "notice_period_days",
    align: "right",
    width: 140,
  },
  { key: "notes", labelKey: "subscriptions.field.notes", width: 260 },
];

export const SUBSCRIPTION_TYPES_TABLE_ID = "subscription_types";

export const SUBSCRIPTION_TYPE_COLUMNS: ColumnMeta[] = [
  { key: "label", labelKey: "common.label_field", sortKey: "label", primary: true, width: 220 },
  // The immutable slug (#234): monospace and short, but long enough to read whole.
  {
    key: "key",
    labelKey: "settings.subscriptions.key",
    sortKey: "key",
    defaultVisible: true,
    width: 180,
  },
  // A count, sized to its header ("Taaksjablonen") rather than to the digit under it.
  {
    key: "tasks",
    labelKey: "settings.subscriptions.task_templates",
    sortKey: "tasks",
    align: "right",
    defaultVisible: true,
    width: 140,
  },
  {
    key: "active",
    labelKey: "subscriptions.field.status",
    sortKey: "active",
    defaultVisible: true,
    width: 110,
  },
];
