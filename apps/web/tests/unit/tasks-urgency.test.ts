/**
 * The dashboard tile's urgency partition (`$lib/modules/tasks/urgency.ts`, #398).
 *
 * Every rule below fails *silently* in a browser, which is the whole reason this file exists.
 * Wire `late` to `?due=week` and the chip still renders, still reads "1 te laat", still links,
 * and still opens a list of plausible tasks — the mistake is only visible to somebody who
 * counts. Same for a zero that draws: it is not an error, it is noise, and noise is what the
 * team asked us to take off this tile in the first place.
 *
 * The API is the other half of the same contract (`tests/test_tasks_api.py`, which asserts each
 * figure against the total of the list its chip opens). This side asserts that the row asks for
 * that list and not for one of the other two.
 *
 * Run with `pnpm web test:unit`.
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { urgencyCounters } from "../../src/lib/modules/tasks/urgency.ts";

describe("urgencyCounters", () => {
  test("each bucket opens the ?due= list that holds exactly what it counted", () => {
    const counters = urgencyCounters({ overdue: 1, due_today: 2, due_week: 3 });

    assert.deepEqual(
      counters.map((counter) => [counter.state, counter.due, counter.count]),
      [
        ["late", "overdue", 1],
        ["today", "today", 2],
        ["soon", "week", 3],
      ],
    );
  });

  test("the order is the urgency ramp, not the payload's field order", () => {
    // Written back-to-front on purpose: the row's order is the module's, so a reshuffled API
    // response cannot quietly put "deze week" in front of "te laat".
    const counters = urgencyCounters({ due_week: 4, due_today: 1, overdue: 9 });

    assert.deepEqual(
      counters.map((counter) => counter.due),
      ["overdue", "today", "week"],
    );
  });

  test("a zero draws nothing — a row with nothing urgent on it carries no counters", () => {
    assert.deepEqual(urgencyCounters({ overdue: 0, due_today: 0, due_week: 0 }), []);
    assert.deepEqual(
      urgencyCounters({ overdue: 0, due_today: 3, due_week: 0 }).map((c) => c.due),
      ["today"],
    );
  });

  test("each counter names its own message key, so no bucket borrows another's words", () => {
    const keys = urgencyCounters({ overdue: 1, due_today: 1, due_week: 1 }).map((c) => c.key);

    assert.deepEqual(keys, [
      "tasks.overdue_count",
      "tasks.due_today_count",
      "tasks.due_week_count",
    ]);
    assert.equal(new Set(keys).size, keys.length);
  });

  test("a row that predates the three figures degrades to no counters, never to NaN", () => {
    // A cached payload, or an API one release behind: `count` still renders, and the tile is
    // exactly the tile it was before this issue rather than a row of "undefined".
    assert.deepEqual(
      urgencyCounters({ overdue: 2 }).map((c) => c.due),
      ["overdue"],
    );
    assert.deepEqual(urgencyCounters(undefined), []);
    assert.deepEqual(urgencyCounters(null), []);
  });
});
