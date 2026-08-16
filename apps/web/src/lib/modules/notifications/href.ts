/**
 * Where a notification opens (issue #16).
 *
 * Its own module, importing **nothing**: this is the one piece of notification rendering that is
 * pure data — no locale, no timezone, no formatter — and keeping it that way is what lets a unit
 * test pin the destination of every event type without a Vite resolver (`tests/unit`).
 */

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

/**
 * Where the notification opens. Every number opens (docs/UX.md, Principle 7).
 *
 * The rule this settled on: **the destination is the thing the sentence is about, and it arrives
 * ready to act on**. A "waiting on your review" line that lands on a queue of forty is a search,
 * not a link, so an id in the URL opens that one record's own surface wherever the host page has
 * one (`?request=`, `?interaction=`, `?week=`) — the shape the leave calendar established.
 *
 * Every ``entity_type`` the fan-out can write answers here, and the two that did not are the
 * reason this is a `satisfies` map rather than a `switch` with a `default: null`: `snelstart`
 * arrived in #377 and `interaction` never linked to the note itself, so both were rendered as
 * plain text in an inbox whose whole job is getting you to the record. A type added to
 * `events.ENTITY_TYPES` without an entry here is a build break, not a dead row.
 */
type HrefResolver = (item: NotificationLike) => string | null;

const HREF_FOR_ENTITY = {
  // A comment event carries the comment it is about (#312), and the task page reads `?comment=`:
  // it expands whatever hides that message, scrolls to it, marks it, and opens the reply box
  // underneath. Without the id, "Jan reageerde op Website migratie" opened a task with fifty
  // comments on it and left the reader to find the ones that were new — the rule at the top of
  // this file ("the destination is the thing the sentence is about"), unapplied to the one event
  // type that names something *inside* a record.
  task: (item) => {
    const comment = item.payload?.comment_id;
    return typeof comment === "string" && comment
      ? `/tasks/${item.entity_id}?comment=${comment}`
      : `/tasks/${item.entity_id}`;
  },
  project: (item) => `/projects/${item.entity_id}`,
  company: (item) => `/companies/${item.entity_id}`,
  // The event decides whose surface answers it: a request waiting on *you* opens the team
  // review (deep-linked, so approve/deny is one click away), a decision about *your* request
  // opens it on your own list — never just "the leave page".
  leave_request: (item) =>
    item.event_type === "leave.requested"
      ? `/leave/team?request=${item.entity_id}`
      : `/leave?request=${item.entity_id}`,
  // A timesheet has no row, so the subject is a person and the week is in the payload. The
  // reminder is *about* a week you did not fill in, and landing on this week instead is asking
  // the reader to navigate back to the one the sentence just named.
  timesheet: (item) => {
    const week = item.payload?.week_start;
    return typeof week === "string" && week ? `/time?week=${week}` : "/time";
  },
  // The note itself, in the detail modal that holds the review controls — for the pending
  // queue too (#156 sent it to `?status=pending`, which is the right *list* and still one more
  // hunt). `status=pending` rides along so the list behind the modal is the queue it came from,
  // and `owner=all` for anything else, because a note a colleague wrote mentioning you is not
  // one of "mijn contactmomenten" and the default filter would hide it.
  interaction: (item) =>
    item.event_type === "interactions.email_pending"
      ? `/interactions?status=pending&interaction=${item.entity_id}`
      : `/interactions?owner=all&interaction=${item.entity_id}`,
  // Not a record anybody opens (the event says so out loud) — the connection's settings screen
  // is where the failure is diagnosed and the credential re-entered.
  snelstart_account: () => "/settings/snelstart",
} satisfies Record<string, HrefResolver>;

export function notificationHref(item: NotificationLike): string | null {
  const resolve: HrefResolver | undefined = (
    HREF_FOR_ENTITY as Record<string, HrefResolver | undefined>
  )[item.entity_type];
  return resolve ? resolve(item) : null;
}
