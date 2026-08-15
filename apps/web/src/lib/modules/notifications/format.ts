/**
 * Rendering a notification (issue #16).
 *
 * The API never ships a translated string: an event carries an `event_type` and a payload of
 * i18n parameters, and the *reader's* locale decides the sentence. This is the one place that
 * turns the pair into text, so the bell, the list and the activity panels always read alike.
 *
 * Two things need translating before the sentence can be built:
 * - a status (`from`/`to`) is a raw vocabulary value whose namespace depends on the entity;
 * - a date is a wall-clock day and must print European (`docs/UX.md`), never an ISO string.
 */
import { fmtDayMonth, fmtLongDay, fmtNumber } from "$lib/core/format";
import { hasMessage, t } from "$lib/core/i18n";
import { getTimeZone } from "$lib/core/timezone";

export interface NotificationLike {
  event_type: string;
  entity_type: string;
  entity_id: string;
  /** Optional because the API gives it a default: an event may carry no parameters at all. */
  payload?: Record<string, unknown>;
  /**
   * Who did it, when a person did — `null` for anything a cron emitted. It decides the
   * *grammar* of the sentence, not merely whether a name is drawn in front of it (#358).
   */
  actor_name?: string | null;
}

/** Each entity type keeps its status vocabulary in its own namespace. */
const STATUS_NAMESPACE: Record<string, string> = {
  task: "tasks.status",
  project: "projects.status",
  company: "companies.status",
};

/**
 * Payload values that must not print as their raw wire form.
 *
 * The old allow-list of four date keys silently missed `scheduled_date` when #188 added it, so a
 * notification read "…in op 2026-08-13" beside a sibling saying "12 jul" — and every future date
 * key would have done the same. The rule is now the key's *shape*: anything ending `_date` whose
 * value is a `yyyy-mm-dd` is a wall-clock day and prints European (`docs/UX.md`). Matching on
 * the value too keeps a key that merely ends in `_date` but carries something else intact.
 */
const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

/**
 * The local calendar day an instant falls on, in the tenant's zone (CLAUDE.md §8).
 *
 * Deliberately `Intl` in the tenant zone, never `iso.slice(0, 10)`: a +02 zone's midnight is
 * 22:00 UTC the day *before* in summer, so slicing groups the evening under yesterday for half
 * the year (docs/UX.md, "Known mistakes"). The formatter is cached per zone, since it is called
 * once per notification.
 */
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

export function localDay(isoDateTime: string): string {
  return dayFormatter().format(new Date(isoDateTime));
}

/** Calendar arithmetic on a `yyyy-mm-dd`, so a 23-hour DST day cannot skip one. */
function previousDay(day: string): string {
  const [year, month, date] = day.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, date - 1)).toISOString().slice(0, 10);
}

/** "Today" / "Yesterday" / "maandag 7 juli" — the heading over a day's notifications. */
export function dayLabel(day: string): string {
  const today = localDay(new Date().toISOString());
  if (day === today) return t("notifications.today");
  if (day === previousDay(today)) return t("notifications.yesterday");
  return fmtLongDay(day);
}

/** The sentence a notification reads as, in the reader's locale. */
export function notificationText(item: NotificationLike): string {
  const payload = item.payload ?? {};
  const params: Record<string, unknown> = { ...payload };

  const namespace = STATUS_NAMESPACE[item.entity_type];
  if (namespace) {
    if (typeof payload.from === "string") params.from = t(`${namespace}.${payload.from}`);
    if (typeof payload.to === "string") params.to = t(`${namespace}.${payload.to}`);
  }
  for (const [key, value] of Object.entries(payload)) {
    if (key.endsWith("_date") && typeof value === "string" && ISO_DAY.test(value)) {
      params[key] = fmtDayMonth(value);
    }
  }
  // `week_start` is the one wall-clock day whose key does not end in `_date`.
  if (typeof payload.week_start === "string" && ISO_DAY.test(payload.week_start)) {
    params.week_start = fmtDayMonth(payload.week_start);
  }
  // Minutes are the API's unit; hours are what a person signs off.
  if (typeof payload.minutes === "number") params.hours = fmtNumber(payload.minutes / 60);

  // An actor-prefixed message ("plande {title} in op …") is a predicate, and a cron has no name
  // to put in front of it — so an event a person *can* emit but a scheduler also emits needs a
  // second, whole-sentence phrasing. `.system` is that phrasing, used only when no actor stands
  // in front of the line; an event that never has one keeps its single key.
  const base = `notifications.event.${item.event_type}`;
  const key = !item.actor_name && hasMessage(`${base}.system`) ? `${base}.system` : base;
  return t(key, params);
}

/** Where the notification opens. Every number opens (docs/UX.md, Principle 7). */
export function notificationHref(item: NotificationLike): string | null {
  switch (item.entity_type) {
    case "task":
      return `/tasks/${item.entity_id}`;
    case "project":
      return `/projects/${item.entity_id}`;
    case "company":
      return `/companies/${item.entity_id}`;
    case "leave_request":
      // The event decides whose surface answers it: a request waiting on *you* opens the
      // team review (deep-linked, so approve/deny is one click away), a decision about
      // *your* request opens it on your own list — never just "the leave page".
      return item.event_type === "leave.requested"
        ? `/leave/team?request=${item.entity_id}`
        : `/leave?request=${item.entity_id}`;
    case "timesheet":
      return "/time";
    case "interaction": {
      // A pending email opens on the review queue (#156) — the place built for deciding.
      if (item.event_type === "interactions.email_pending") return "/interactions?status=pending";
      // Anything else opens where its timeline lives: the most specific host it hangs on
      // (#151 mentions carry task/project links too).
      const payload = item.payload ?? {};
      for (const [key, prefix] of [
        ["task_id", "/tasks/"],
        ["project_id", "/projects/"],
        ["company_id", "/companies/"],
        ["contact_id", "/contacts/"],
      ] as const) {
        const value = payload[key];
        if (typeof value === "string" && value) return `${prefix}${value}`;
      }
      return null;
    }
    default:
      return null;
  }
}

/** The record a notification is about, named from the snapshot the event carried. */
export function notificationSubject(item: NotificationLike): string {
  const title = item.payload?.title;
  if (typeof title === "string" && title) return title;
  return t(`notifications.entity.${item.entity_type}`);
}
