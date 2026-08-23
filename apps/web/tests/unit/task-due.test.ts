/**
 * Where "vandaag", "deze week" and "later" stop (`$lib/modules/tasks/due.ts`, #397).
 *
 * The tile used to partition into three and file this afternoon's week, next month and every
 * undated task under one heading. The four-bucket replacement is only worth anything if the
 * boundaries hold in the places nobody looks at while building it — the day either side of each
 * edge, and the undated row that has no edge at all — because a bucket that is one day out looks
 * exactly like a bucket that is right on the afternoon a developer opens the dashboard.
 *
 * The API's `?due=` filter uses these same four names with these same boundaries
 * (`apps/api/app/modules/tasks/service.py`, pinned by `test_task_due_buckets.py`), so a section
 * heading and the list it opens count the same rows.
 *
 * Run with `pnpm web test:unit`.
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  dayDistance,
  DUE_BUCKETS,
  dueBucket,
  dueHref,
  dueState,
  groupByDue,
  WEEK_HORIZON,
} from "../../src/lib/modules/tasks/due.ts";

const TODAY = "2026-07-07"; // a Tuesday, so "deze week" is not a calendar week here

describe("the four buckets", () => {
  test("yesterday is over tijd and today is not", () => {
    assert.equal(dueBucket("2026-07-06", TODAY), "overdue");
    assert.equal(dueBucket(TODAY, TODAY), "today");
  });

  test("tomorrow starts deze week, and today does not belong to it", () => {
    assert.equal(dueBucket("2026-07-08", TODAY), "week");
    assert.notEqual(dueBucket(TODAY, TODAY), "week");
  });

  test("the seventh day is still deze week and the eighth is later", () => {
    assert.equal(dueBucket("2026-07-14", TODAY), "week"); // today + 7
    assert.equal(dueBucket("2026-07-15", TODAY), "later"); // today + 8
  });

  test("a task with no deadline is later, not dropped", () => {
    // The old `upcoming` swept these up beside next Tuesday's work; the bucket they land in is a
    // decision, and the one thing they may never do is fall out of the partition.
    assert.equal(dueBucket(null, TODAY), "later");
    assert.equal(dueBucket(undefined, TODAY), "later");
  });

  test("the four cover every task exactly once", () => {
    const rows = [
      { id: "a", due_date: "2026-06-30" },
      { id: "b", due_date: TODAY },
      { id: "c", due_date: "2026-07-10" },
      { id: "d", due_date: "2026-09-01" },
      { id: "e", due_date: null },
    ];
    const groups = groupByDue(rows, TODAY);
    const total = DUE_BUCKETS.reduce((sum, bucket) => sum + groups[bucket].length, 0);
    assert.equal(total, rows.length);
    assert.deepEqual(
      groups.overdue.map((r) => r.id),
      ["a"],
    );
    assert.deepEqual(
      groups.today.map((r) => r.id),
      ["b"],
    );
    assert.deepEqual(
      groups.week.map((r) => r.id),
      ["c"],
    );
    assert.deepEqual(
      groups.later.map((r) => r.id),
      ["d", "e"],
    );
  });

  test("a bucket keeps the order the API sent, which is date then priority", () => {
    const rows = [
      { id: "first", due_date: "2026-07-09" },
      { id: "second", due_date: "2026-07-08" },
    ];
    assert.deepEqual(
      groupByDue(rows, TODAY).week.map((r) => r.id),
      ["first", "second"],
    );
  });
});

describe("the arithmetic under the boundaries", () => {
  test("a distance counts whole days in either direction", () => {
    assert.equal(dayDistance(TODAY, "2026-07-10"), 3);
    assert.equal(dayDistance(TODAY, TODAY), 0);
    assert.equal(dayDistance(TODAY, "2026-07-04"), -3);
  });

  test("it survives the clock change, which local-midnight arithmetic does not", () => {
    // 25 October 2026 is the night the Dutch clocks go back: 24 → 26 October is two days, and a
    // `new Date(...)` built in local time would answer 2.04, which `Math.round` only rescues by
    // accident. Both ends are parsed as UTC midnights, so the hour never enters the sum.
    assert.equal(dayDistance("2026-10-24", "2026-10-26"), 2);
    assert.equal(dueBucket("2026-10-31", "2026-10-24"), "week"); // exactly WEEK_HORIZON out
    assert.equal(WEEK_HORIZON, 7);
  });
});

describe("a heading is a link and a colour", () => {
  test("every bucket opens the list it totals, under its own name", () => {
    for (const bucket of DUE_BUCKETS) {
      assert.equal(dueHref(bucket), `/tasks?due=${bucket}`);
    }
  });

  test("only the two buckets that are claims are tinted, so the tint is hierarchy", () => {
    // *Deze week* was `soon` until the board (#395) drew all four headings adjacent and at
    // 10px uppercase: red, orange and amber do not separate, and three warm headings read as
    // one long warning — the *"rustiger gebruik van kleuren"* half of the same complaint. Only
    // the moment that has passed and the moment that is now shout; `neutral` is still *drawn*
    // (the theme's own text), which is what keeps it above the muted grey *Later* keeps.
    assert.deepEqual(DUE_BUCKETS.map(dueState), ["late", "today", "neutral", "neutral"]);
  });
});
