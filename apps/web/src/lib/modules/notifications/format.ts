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
import { t } from "$lib/core/i18n";

export interface NotificationLike {
  event_type: string;
  entity_type: string;
  entity_id: string;
  /** Optional because the API gives it a default: an event may carry no parameters at all. */
  payload?: Record<string, unknown>;
}

/** Each entity type keeps its status vocabulary in its own namespace. */
const STATUS_NAMESPACE: Record<string, string> = {
  task: "tasks.status",
  project: "projects.status",
  company: "companies.status",
};

/** Date-only payload keys, printed as a European day rather than an ISO string. */
const DATE_KEYS = ["due_date", "start_date", "end_date", "week_start"] as const;

/**
 * The local calendar day an instant falls on.
 *
 * Deliberately `Intl` in `Europe/Amsterdam`, never `iso.slice(0, 10)`: Amsterdam's midnight is
 * 22:00 UTC the day *before* in summer, so slicing groups the evening under yesterday for half
 * the year (docs/UX.md, "Known mistakes").
 */
const AMSTERDAM_DAY = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Europe/Amsterdam",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

export function localDay(isoDateTime: string): string {
  return AMSTERDAM_DAY.format(new Date(isoDateTime));
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
  for (const key of DATE_KEYS) {
    const value = payload[key];
    if (typeof value === "string") params[key] = fmtDayMonth(value);
  }
  // Minutes are the API's unit; hours are what a person signs off.
  if (typeof payload.minutes === "number") params.hours = fmtNumber(payload.minutes / 60);

  return t(`notifications.event.${item.event_type}`, params);
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
      return "/leave";
    case "timesheet":
      return "/time";
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
