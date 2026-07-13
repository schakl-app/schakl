/** Shared presentation helpers for contactmomenten rows. */
import { Mail, Phone, StickyNote, Users } from "@lucide/svelte";
import type { Component } from "svelte";

import { getTimeZone } from "$lib/core/timezone";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const KIND_ICONS: Record<string, Component<any>> = {
  email: Mail,
  meeting: Users,
  call: Phone,
  note: StickyNote,
};

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
