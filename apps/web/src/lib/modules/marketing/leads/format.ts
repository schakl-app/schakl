/**
 * How a leads widget's numbers and words print — one place, read by every widget shape.
 */
import { fmtDayMonthYear, fmtNumber } from "$lib/core/format";
import { t } from "$lib/core/i18n";

import { fmtCurrency, fmtPercent } from "../format";
import type { LeadColumn, LeadRow, LeadWidget, Unit } from "./types";

/** A value in its unit. `null` prints as a dash: "not computable" is not "zero". */
export function fmtUnit(
  value: number | null | undefined,
  unit: Unit,
  currency?: string | null,
): string {
  if (value === null || value === undefined) return "–";
  switch (unit) {
    case "money":
      return fmtCurrency(value, currency);
    case "percent":
      return fmtPercent(value / 100);
    case "ratio":
      return fmtPercent(value);
    case "text":
      return String(value);
    default:
      return fmtNumber(value, Number.isInteger(value) ? 0 : 1);
  }
}

export function columnTitle(column: LeadColumn): string {
  if (column.title) return column.title;
  if (column.title_key) return t(column.title_key);
  return column.key;
}

/** What a cell holds: a number in `values`, a pivot cell, or a text cell. */
export function cellValue(row: LeadRow, column: LeadColumn): number | string | null {
  if (column.unit === "text") return row.texts?.[column.key] ?? "";
  if (row.cells && column.key in row.cells) return row.cells[column.key];
  return row.values[column.key] ?? null;
}

export function fmtCell(row: LeadRow, column: LeadColumn, currency?: string | null): string {
  const value = cellValue(row, column);
  if (typeof value === "string") return value;
  return fmtUnit(value, column.unit, currency);
}

/** Conditional formatting: the class a cell earns from its column's thresholds. */
export function cellTone(row: LeadRow, column: LeadColumn): "alarm" | "warn" | null {
  const value = cellValue(row, column);
  if (typeof value !== "number") return null;
  if (column.alarm_above != null && value >= column.alarm_above) return "alarm";
  if (column.warn_above != null && value >= column.warn_above) return "warn";
  return null;
}

export function widgetTitle(widget: LeadWidget): string {
  return t(widget.title_key);
}

/** The reason a widget is missing, in words the manager can act on. */
export function unavailableReason(reason: string): string {
  const [code, arg] = reason.split(":", 2);
  switch (code) {
    case "missing_role":
      return t("marketing.leads.unavailable.missing_role", {
        role: t(`marketing.leads.role.${arg}`),
      });
    case "missing_dimension":
      return t("marketing.leads.unavailable.missing_dimension", {
        dimension: t(`marketing.leads.dimension.${arg}`),
      });
    case "report_failed":
      return t("marketing.leads.unavailable.report_failed");
    default:
      return t(`marketing.leads.unavailable.${code}`);
  }
}

export function channelGroupLabel(group: string): string {
  return t(`marketing.leads.group.${group}`);
}

export function breakpointLabel(date: string, text: string | null | undefined): string {
  return text ? `${fmtDayMonthYear(date)} · ${text}` : fmtDayMonthYear(date);
}

/** A table as CSV: the first column is the row label, the rest the widget's columns. */
export function widgetCsv(widget: LeadWidget, labelHeader: string): string {
  const escape = (value: string) => `"${value.replace(/"/g, '""')}"`;
  const header = [labelHeader, ...widget.columns.map(columnTitle)].map(escape).join(";");
  const lines = widget.rows.map((row) =>
    [
      row.label,
      ...widget.columns.map((column) => {
        const value = cellValue(row, column);
        if (value === null) return "";
        return typeof value === "number" ? String(value) : value;
      }),
    ]
      .map(escape)
      .join(";"),
  );
  return [header, ...lines].join("\n");
}

/** Hand the reader a file. The name says what and when, so two exports do not collide. */
export function downloadCsv(filename: string, csv: string): void {
  const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
