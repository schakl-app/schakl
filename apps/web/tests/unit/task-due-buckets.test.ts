/**
 * The urgency vocabulary (`$lib/modules/tasks/due.ts`, #395) — the board's half. The tile's half
 * is `task-due.test.ts` (#397); they read one module, which is the point of both.
 *
 * Three things here are invisible in review and in a screenshot, which is why they are pinned.
 *
 * **"Deze week" is the next seven days, and the boundary is asserted on every weekday.** It is
 * the window the API's `?due=week` has always meant, so a heading and the filter chip beside it
 * count the same rows. A calendar week ending on Sunday reads better on a Monday and collapses
 * to nothing on a Friday afternoon; a test taken on one weekday cannot tell the two rules apart,
 * which is why every assertion below is run against a whole week of "today"s.
 *
 * **The board and the tiles agree by construction.** They used to hold three private copies of
 * `due_date < today`, and the tile's was subtly not the list's. The last block asserts that one
 * partition covers every row exactly once, which is the property a second copy breaks first.
 *
 * **Every deadline gets a distance.** `18 aug` alone asks the reader to know today's date.
 *
 * Run with `pnpm web test:unit`.
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { isoAddDays } from "../../src/lib/core/isodate.ts";
import {
  DUE_BUCKETS,
  DUE_SECTIONS,
  DUE_STATE,
  dueBucket,
  dueDistance,
  dueSection,
  weekEnd,
} from "../../src/lib/modules/tasks/due.ts";

// A whole week of "today"s, so no rule below is asserted only on the weekday it happens to be
// right on. 2026-08-17 is a Monday.
const MONDAY = "2026-08-17";
const WEEK = {
  monday: "2026-08-17",
  tuesday: "2026-08-18",
  wednesday: "2026-08-19",
  thursday: "2026-08-20",
  friday: "2026-08-21",
  saturday: "2026-08-22",
  sunday: "2026-08-23",
};

describe("the week is the next seven days, on whatever day you are reading", () => {
  test("the horizon is seven days out from today, every day of the week", () => {
    for (const [name, day] of Object.entries(WEEK)) {
      assert.equal(weekEnd(day), isoAddDays(day, 7), name);
      // The seventh day is inside the bucket and the eighth is not — the boundary itself, which
      // an off-by-one in either direction moves silently.
      assert.equal(dueBucket(isoAddDays(day, 7), day), "week", name);
      assert.equal(dueBucket(isoAddDays(day, 8), day), "later", name);
    }
  });

  test("a Friday reader still has a week left, which a calendar week would not give them", () => {
    // The day people plan is the day a Monday–Sunday bucket is emptiest; this one is not.
    assert.equal(dueBucket(WEEK.saturday, WEEK.friday), "week");
    assert.equal(dueBucket(WEEK.sunday, WEEK.friday), "week");
    assert.equal(dueBucket("2026-08-27", WEEK.friday), "week"); // the Thursday after
  });

  test("the horizon rolls over a month and a year boundary", () => {
    assert.equal(weekEnd("2026-12-30"), "2027-01-06");
    assert.equal(dueBucket("2027-01-02", "2026-12-30"), "week");
    assert.equal(dueBucket("2027-01-07", "2026-12-30"), "later");
  });
});

describe("the four buckets", () => {
  test("yesterday is overdue, today is today, tomorrow is this week", () => {
    assert.equal(dueBucket("2026-08-18", WEEK.wednesday), "overdue");
    assert.equal(dueBucket(WEEK.wednesday, WEEK.wednesday), "today");
    assert.equal(dueBucket(WEEK.thursday, WEEK.wednesday), "week");
  });

  test("an overdue task is overdue however old, and never falls out of the vocabulary", () => {
    assert.equal(dueBucket("2019-01-01", MONDAY), "overdue");
  });

  test("no deadline at all is *later*, not overdue", () => {
    // The column stayed nullable for a release (#392, expand/contract), so every instance
    // upgrades carrying rows written before the deadline became required. Filing them under
    // "over tijd" would paint a backlog nobody has scheduled in alarm red.
    assert.equal(dueBucket(null, MONDAY), "later");
    assert.equal(dueBucket(undefined, MONDAY), "later");
    assert.equal(dueBucket("", MONDAY), "later");
  });

  test("a finished task has no urgency, whatever its date says", () => {
    assert.equal(dueSection("2019-01-01", MONDAY, false), "overdue");
    assert.equal(dueSection("2019-01-01", MONDAY, true), "done");
    assert.equal(dueSection(MONDAY, MONDAY, true), "done");
  });
});

describe("one partition, so a tile and the board cannot disagree", () => {
  const rows = [
    "2019-01-01",
    "2026-08-16",
    WEEK.monday,
    WEEK.tuesday,
    WEEK.sunday,
    "2026-08-24",
    "2027-05-05",
    null,
  ];

  test("every row lands in exactly one bucket, for every day of the week", () => {
    for (const today of Object.values(WEEK)) {
      const counted = DUE_BUCKETS.map(
        (bucket) => rows.filter((due) => dueBucket(due, today) === bucket).length,
      ).reduce((a, b) => a + b, 0);
      assert.equal(counted, rows.length, today);
    }
  });

  test("the board's sections are the buckets plus finished, in reading order", () => {
    assert.deepEqual([...DUE_SECTIONS], [...DUE_BUCKETS, "done"]);
    assert.deepEqual([...DUE_BUCKETS], ["overdue", "today", "week", "later"]);
  });
});

describe("the section headings are the state palette's, never the tenant's brand", () => {
  test("only the two headings that are claims are tinted", () => {
    assert.equal(DUE_STATE.overdue, "late");
    assert.equal(DUE_STATE.today, "today");
    // Not `soon`. At 10px uppercase, red, orange and amber do not separate: three warm headings
    // down one board read as one long warning, which is the half of this complaint about colour
    // being too loud rather than too quiet. Only what has passed and what is now shouts.
    assert.equal(DUE_STATE.week, "neutral");
    assert.equal(DUE_STATE.later, null);
    assert.equal(DUE_STATE.done, null);
  });

  test("no section is drawn in the tenant's brand", () => {
    // A brand-coloured state says a different thing per tenant (#404) — on the gold one it is
    // indistinguishable from an amber warning, which is exactly the screen this issue is about.
    for (const state of Object.values(DUE_STATE)) {
      if (state) assert.doesNotMatch(state, /brand|accent/);
    }
  });

  test("every state named is one the palette knows", () => {
    const known = ["late", "today", "soon", "ok", "neutral"];
    for (const section of DUE_SECTIONS) {
      const state = DUE_STATE[section];
      assert.ok(state === null || known.includes(state), section);
    }
  });
});

describe("a deadline always prints its distance", () => {
  test("the three short forms read as words, not as arithmetic", () => {
    assert.deepEqual(dueDistance(MONDAY, MONDAY), { key: "tasks.due.rel.today", count: 0 });
    assert.deepEqual(dueDistance(WEEK.tuesday, MONDAY), {
      key: "tasks.due.rel.tomorrow",
      count: 1,
    });
    assert.deepEqual(dueDistance("2026-08-16", MONDAY), {
      key: "tasks.due.rel.late_one",
      count: 1,
    });
  });

  test("plurals are a key pair, never an ICU plural", () => {
    // Paraglide does not parse `{n, plural, …}` in this project: it compiles to garbage that
    // renders rather than failing, so the singular and the plural are separate keys.
    assert.equal(dueDistance("2026-08-14", MONDAY)?.key, "tasks.due.rel.late_other");
    assert.equal(dueDistance("2026-08-14", MONDAY)?.count, 3);
    assert.equal(dueDistance("2026-08-21", MONDAY)?.key, "tasks.due.rel.in_days");
    assert.equal(dueDistance("2026-08-21", MONDAY)?.count, 4);
  });

  test("the count is a whole number of days across a DST change", () => {
    // The Dutch clocks go back on 25 October 2026, so that week is 169 hours long. A distance
    // computed by dividing milliseconds without rounding would answer 6.96 days.
    assert.equal(dueDistance("2026-10-28", "2026-10-21")?.count, 7);
  });
});
