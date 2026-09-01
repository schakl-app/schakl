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
  /**
   * The same body with its formatting kept, set **only** for an e-mail whose HTML part the API
   * converted itself. Render this when it is there and `body_text` when it is not: a plain-text
   * message is not markdown, and drawing it as such would turn a sender's `*sterretjes*` into
   * italics. Rides `with_body` exactly like `body_text` — a list row carries neither.
   */
  body_markdown?: string | null;
  direction: string;
  company_id?: string | null;
  project_id?: string | null;
  task_id?: string | null;
  /** The **lead** contact — chip 0 of `contacts` (#300). Read `contacts` for who it was with. */
  contact_id?: string | null;
  /** Everyone the moment was with, in chip order, labelled by the API (#300). */
  contacts?: { id: string; name?: string | null }[];
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
  /** Gmail-style conversation grouping (#272): the id every logged email row of one thread
   *  shares. `null`/absent on manual/pending rows — each is its own singleton. */
  conversation_id?: string | null;
  /** The Gmail conversation this row came from — what "mist er een bericht?" asks about (#342). */
  gmail_thread_id?: string | null;
  /** How many messages this conversation folds — a badge shows only when it is > 1, and it
   *  decides whether the detail modal fetches the full thread. Defaults to 1. */
  conversation_count?: number;
  /**
   * The still-unreviewed messages this row stands for in the viewer's own mailbox — its whole
   * pending Gmail thread, oldest first, itself included. The review queue folds a thread to one
   * row exactly as the timeline folds a logged conversation, and this is what a thread-level
   * approve / file / reject acts on. Empty on a logged or manual row.
   */
  review_ids?: string[];
  deep_link: string | null;
}

/**
 * What a review acts on when it acts on this row: a pending row's whole pending thread, or the
 * row itself. Every bulk selection expands through this, so ticking one folded row on the queue
 * approves, files or rejects the conversation it stands for — which is what the row says it is.
 */
export function reviewIds(item: InteractionItem): string[] {
  return item.status === "pending" && item.review_ids?.length ? item.review_ids : [item.id];
}

/**
 * Who a folded conversation is with, for the queue row: the outside addresses (a colleague on
 * Cc is not who a thread is *with*), by name where the header carried one, deduplicated, three
 * at most — the rest are counted so a long Cc list reads as "+4" rather than a second line.
 *
 * "Outside" is decided by the contact match before the member match: a client's contact person
 * with a portal login resolves as *both* (the member pass joins every membership, the `client`
 * role's included), and a person the CRM files as a contact is who the thread is with whether
 * or not they can sign in.
 */
export function participantNames(
  item: InteractionItem,
  max = 3,
): { names: string[]; more: number } {
  const seen = new Set<string>();
  const names: string[] = [];
  for (const p of item.participants ?? []) {
    if (p.user_id && !p.contact_id) continue;
    const key = p.email.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    names.push(p.name || p.email);
  }
  return { names: names.slice(0, max), more: Math.max(0, names.length - max) };
}

/**
 * Is this row a real email message — synced from Gmail or uploaded as a `.eml` (#262)?
 *
 * Such a body is *received text*, so it renders as it arrived (line breaks kept, never parsed
 * as our markdown) and carries the attachments the message came with. A `manual` row's body is
 * the author's own note.
 */
/**
 * The people a row names, as chips (#300) — one place, because five surfaces draw them.
 *
 * Falls back to the lead pair when `contacts` is absent, which is what a panel payload from an
 * older API build (or a cached page) carries: the chip is the same fact at an earlier age, and
 * dropping it would silently blank the column instead of showing one name.
 */
export function contactChips(
  item: Pick<InteractionItem, "contacts" | "contact_id" | "contact_name">,
): { href: string; label: string }[] {
  const people = item.contacts?.length
    ? item.contacts
    : item.contact_id
      ? [{ id: item.contact_id, name: item.contact_name }]
      : [];
  return people
    .filter((person) => person.name)
    .map((person) => ({ href: `/contacts/${person.id}`, label: person.name as string }));
}

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

/**
 * The full row behind a list row, fetched before an edit form opens (#290).
 *
 * A list row carries no `body_text`: twenty full e-mail bodies to draw a snippet column was the
 * bulk of the list response. The edit form **posts** that field, so opening it on a list row
 * would submit an empty body and wipe the notes — this is what keeps the saving safe rather
 * than merely cheap. On a failed fetch the caller gets the row it had, and the form falls back
 * to the old behaviour instead of opening on nothing.
 */
export async function withBody(item: InteractionItem): Promise<InteractionItem> {
  if (item.body_text != null) return item;
  const response = await fetch(`/api/v1/interactions/${item.id}`, {
    headers: { accept: "application/json" },
  });
  return response.ok ? { ...item, ...((await response.json()) as InteractionItem) } : item;
}
