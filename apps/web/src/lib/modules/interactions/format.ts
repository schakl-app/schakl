/** Shared presentation helpers for contactmomenten rows. */
import { Mail, MapPin, MessageSquare, Phone, StickyNote, Users, Video } from "@lucide/svelte";
import type { Component } from "svelte";

import { fmtLongDay } from "$lib/core/format";
import { t } from "$lib/core/i18n";
import { getTimeZone } from "$lib/core/timezone";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const KIND_ICONS: Record<string, Component<any>> = {
  email: Mail,
  meeting: Users, // pre-#174 rows on a rolled-back schema; the split kinds carry their own
  online_meeting: Video,
  physical_meeting: MapPin,
  call: Phone,
  note: StickyNote,
};

/** Kinds are tenant-defined (#174): known keys keep their icon, new ones get a generic one. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function kindIcon(key: string): Component<any> {
  return KIND_ICONS[key] ?? MessageSquare;
}

/** One tenant-configurable interaction kind, as `/api/v1/interactions/kinds` returns it. */
export interface InteractionKindDef {
  id: string;
  key: string;
  label_i18n?: Record<string, string>;
  position: number;
  active: boolean;
}

/** A kind's label in the viewer's locale — tenant data first, seeded locales as fallback. */
export function kindLabel(def: InteractionKindDef, locale: string): string {
  return def.label_i18n?.[locale] || def.label_i18n?.nl || def.label_i18n?.en || def.key;
}

/** The one kind a person may never type by hand (#174) — only the gmail feed and the `.eml`
 *  upload path (#262) write it, and both parse a real message rather than accepting one. */
export const PROTECTED_KIND = "email";

let kindsCache: InteractionKindDef[] | null = null;

/** Every kind the org has (inactive included so an edited row can keep its deactivated kind),
 *  fetched once per session and shared by every form instance — the create modal must not cost
 *  every host page an SSR call. */
export async function interactionKinds(): Promise<InteractionKindDef[]> {
  if (kindsCache === null) {
    const response = await fetch("/api/v1/interactions/kinds?include_inactive=true", {
      headers: { accept: "application/json" },
    });
    kindsCache = response.ok ? await response.json() : [];
  }
  return kindsCache ?? [];
}

/** The kinds the manual form may offer — everything except the protected `email`. */
export async function manualKinds(): Promise<InteractionKindDef[]> {
  return (await interactionKinds()).filter((k) => k.key !== PROTECTED_KIND);
}

export interface InteractionItem {
  id: string;
  kind: string;
  status: string;
  occurred_at: string;
  subject: string | null;
  snippet: string | null;
  body_text?: string | null;
  direction: string;
  company_id?: string | null;
  project_id?: string | null;
  task_id?: string | null;
  contact_id?: string | null;
  /** Labels of the linked records (#147), resolved by the API — the row chips read these. */
  company_name?: string | null;
  project_name?: string | null;
  task_title?: string | null;
  contact_name?: string | null;
  owner_user_id: string | null;
  owner_name: string | null;
  /** This moment is a task's designated closing contact moment (#157) — the API resolves it. */
  closes_task?: boolean;
  participants?: {
    email: string;
    name?: string | null;
    role?: string;
    /** The org contact this address resolves to, matched by the API at read time (#160). */
    contact_id?: string | null;
    /** The org member (colleague) this address resolves to (#167) — never a contact-create. */
    user_id?: string | null;
  }[];
  source: string;
  deep_link: string | null;
}

/**
 * Is this row a real email message — synced from Gmail or uploaded as a `.eml` (#262)?
 *
 * Such a body is *received text*, so it renders as it arrived (line breaks kept, never parsed
 * as our markdown) and carries the attachments the message came with. A `manual` row's body is
 * the author's own note.
 */
export function isMailRow(item: Pick<InteractionItem, "source">): boolean {
  return item.source === "gmail" || item.source === "upload";
}

/** Only a Gmail-sourced row belongs to the mailbox owner's review flow (approve / reject /
 *  remap, and no ordinary edit). An uploaded email is an ordinary row of its owner's. */
export function isGmailRow(item: Pick<InteractionItem, "source">): boolean {
  return item.source === "gmail";
}

const _dayFmt = new Map<string, Intl.DateTimeFormat>();

function dayFormatter(): Intl.DateTimeFormat {
  const tz = getTimeZone();
  let formatter = _dayFmt.get(tz);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat("en-CA", {
      timeZone: tz,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
    _dayFmt.set(tz, formatter);
  }
  return formatter;
}

/** An instant's local calendar day (`yyyy-mm-dd`) in the org zone — the day-group key. */
export function localDay(isoDateTime: string): string {
  return dayFormatter().format(new Date(isoDateTime));
}

function previousDay(day: string): string {
  const [year, month, date] = day.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, date - 1)).toISOString().slice(0, 10);
}

/** "Today" / "Yesterday" / "maandag 7 juli" — the heading over a day's interactions. */
export function dayLabel(day: string): string {
  const today = localDay(new Date().toISOString());
  if (day === today) return t("common.today");
  if (day === previousDay(today)) return t("common.yesterday");
  return fmtLongDay(day);
}

/**
 * An instant, split into the tenant zone's wall-clock date + time — what the edit form's
 * `DateInput`/`TimeInput` prefill with. The API interprets the naive value it gets back in the
 * same zone, so a round-trip without edits stores the same instant.
 */
export function instantToLocal(iso: string): { date: string; time: string } {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: getTimeZone(),
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(iso));
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return {
    date: `${get("year")}-${get("month")}-${get("day")}`,
    time: `${get("hour")}:${get("minute")}`,
  };
}
