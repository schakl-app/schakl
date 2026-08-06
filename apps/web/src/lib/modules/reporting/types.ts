/**
 * Web-side shapes for the reporting API (issue #300), taken from the generated client.
 *
 * Derived rather than hand-written: a report carries a lot of fields and a snapshot whose shape
 * the sections decide, so re-typing it by hand would drift the first time a section grew a
 * column. `pnpm gen:client` keeps these honest.
 */
import type { components } from "$lib/core/api/schema";

export type ReportRow = components["schemas"]["ReportRow"];
export type ReportDetail = components["schemas"]["ReportDetail"];
export type ReportList = components["schemas"]["ReportList"];
export type ReportProfile = components["schemas"]["ReportProfileRead"];
export type ReportTone = components["schemas"]["ReportToneRead"];
export type ReportTemplate = components["schemas"]["ReportTemplateRead"];
export type ReportingSettings = components["schemas"]["ReportingSettingsRead"];
export type SectionCatalogEntry = components["schemas"]["SectionCatalogEntry"];
export type ReportRecipient = components["schemas"]["ReportRecipient"];

export type ReportStatus = "draft" | "generating" | "ready" | "sent" | "failed";
export type ReportAudience = "client" | "internal";
export type ReportDelivery = "review" | "auto";
export type ReportCadence = "off" | "monthly" | "quarterly";

/** One `{key, title}` from `ReportDetail.sections`, in the order the document prints them. */
export interface ReportSectionRef {
  key: string;
  title: string;
}

/** One entry of `Report.warnings` — the agency's own notes about a run. Never shown to a client. */
export interface ReportWarning {
  code: string;
  detail?: string;
}

/**
 * What `app/core/narratives.py` lends the marketing panel: the latest *published* report's own
 * words, and which period they describe. Dated on purpose — a paragraph about July must never
 * read as a description of today.
 */
export interface BorrowedNarrative {
  report_id: string;
  period_label: string;
  summary: string;
  sections: Record<string, string>;
}

/** The effective schedule after profile → org → product defaults have been folded together. */
export interface EffectiveSchedule {
  cadence?: ReportCadence | null;
  day_of_month?: number | null;
  hour?: number | null;
  compare?: "year" | "previous" | null;
  delivery?: ReportDelivery | null;
  publish_to_portal?: boolean | null;
}
