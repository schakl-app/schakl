import { t } from "$lib/core/i18n";

import type { ImpexColumn } from "./actions.server";

/**
 * What a human calls one import column.
 *
 * A tenant's own custom field carries tenant labels (§13 data, resolved here — the API never
 * picks a locale for someone else's content); everything else has an i18n key. `t` returns the
 * key itself when a message is missing, so an unlabelled column is still readable.
 *
 * Shared because the mapping step and the error list must agree: a report that names
 * `client_number` after the user picked "Klantnummer" makes them hunt for the column they just
 * chose by a different name.
 */
export function columnLabel(column: ImpexColumn, locale: string): string {
  const labels = (column.label_i18n ?? {}) as Record<string, string>;
  if (column.label_i18n) {
    return labels[locale] || labels.nl || labels.en || column.key;
  }
  return column.label_key ? t(column.label_key) : column.key;
}

/** The same, from a raw column key — what an `ImportRowError.field` carries. */
export function keyLabel(key: string, columns: ImpexColumn[], locale: string): string {
  const column = columns.find((c) => c.key === key);
  return column ? columnLabel(column, locale) : key;
}
