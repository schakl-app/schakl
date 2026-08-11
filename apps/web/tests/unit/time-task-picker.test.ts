/**
 * What the hour-entry task picker offers, and what it merely lets you find.
 *
 * A picker that lists every task an org ever finished is not visibly broken: the dropdown looks
 * fine, and the cost lands months later as hours booked on a task that closed in March. The
 * three rules that keep it honest are all invisible on screen — a finished task is *reachable*
 * rather than offered, the task an entry is already on is offered whatever its status, and an
 * unloaded status vocabulary means "don't know" rather than "everything is open".
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { splitTaskOptions } from "../../src/lib/modules/time/task-picker.ts";

const STATUSES = [
  { key: "open", name: "Open", is_terminal: false },
  { key: "in_progress", name: "In behandeling", is_terminal: false },
  { key: "done", name: "Gereed", is_terminal: true },
];

const LABELS = {
  due: (iso: string) => `deadline ${iso}`,
  allocated: (minutes: number) => `${minutes}m`,
};

const TASKS = [
  { id: "a", title: "Nieuwsbrief mei", status: "open", project_id: "p1" },
  { id: "b", title: "Nieuwsbrief april", status: "done", project_id: "p1" },
  { id: "c", title: "Losse taak", status: "in_progress", project_id: null },
  { id: "d", title: "Ander project", status: "open", project_id: "p2" },
];

const split = (overrides = {}) =>
  splitTaskOptions(TASKS, { statuses: STATUSES, labels: LABELS, ...overrides });

const ids = (options: { value: string }[]) => options.map((o) => o.value);

describe("splitTaskOptions", () => {
  test("a finished task leaves the dropdown but stays findable", () => {
    const { open, closed } = split();
    assert.deepEqual(ids(open), ["a", "c", "d"]);
    assert.deepEqual(ids(closed), ["b"]);
  });

  test("the task the entry is already on is offered, finished or not", () => {
    const { open, closed } = split({ selectedId: "b" });
    assert.ok(ids(open).includes("b"), "editing an entry must show what it is booked on");
    assert.deepEqual(ids(closed), []);
  });

  test("no status vocabulary means nothing is known to be finished", () => {
    const { open, closed } = splitTaskOptions(TASKS, { statuses: [], labels: LABELS });
    assert.deepEqual(ids(open), ["a", "b", "c", "d"]);
    assert.deepEqual(ids(closed), []);
  });

  test("`is_terminal` decides, not the key: a tenant may rename or retire `done`", () => {
    const renamed = [
      { key: "open", name: "Open", is_terminal: false },
      { key: "done", name: "Gereed", is_terminal: false },
      { key: "in_progress", name: "Gefactureerd", is_terminal: true },
    ];
    const { open, closed } = splitTaskOptions(TASKS, { statuses: renamed, labels: LABELS });
    assert.deepEqual(ids(open), ["a", "b", "d"]);
    assert.deepEqual(ids(closed), ["c"]);
  });

  test("a picked project narrows the list but keeps project-less tasks", () => {
    const { open, closed } = split({ projectId: "p1" });
    assert.deepEqual(ids(open), ["a", "c"]);
    assert.deepEqual(ids(closed), ["b"]);
  });

  test("the narrowing applies to the searchable bucket too", () => {
    const { closed } = split({ projectId: "p2" });
    assert.deepEqual(ids(closed), [], "another project's finished task is not this one's");
  });

  test("the hint names the finished status, the deadline and the allocation, in that order", () => {
    const { open, closed } = splitTaskOptions(
      [
        { id: "a", title: "Open met deadline", status: "open", due_date: "2026-07-07" },
        {
          id: "b",
          title: "Afgerond",
          status: "done",
          due_date: "2026-03-01",
          allocated_minutes: 90,
        },
        { id: "c", title: "Kaal", status: "open" },
      ],
      { statuses: STATUSES, labels: LABELS },
    );
    assert.equal(open[0].hint, "deadline 2026-07-07");
    assert.equal(closed[0].hint, "Gereed · deadline 2026-03-01 · 90m");
    assert.equal(open[1].hint, undefined, "a task with nothing to say says nothing");
  });

  test("an open task never carries a status name — the hint is for what is not on offer", () => {
    const { open } = splitTaskOptions([{ id: "a", title: "Bezig", status: "in_progress" }], {
      statuses: STATUSES,
      labels: LABELS,
    });
    assert.equal(open[0].hint, undefined);
  });

  test("the lookup's order survives both buckets", () => {
    const { open } = splitTaskOptions(
      [
        { id: "z", title: "Zebra", status: "open" },
        { id: "a", title: "Aap", status: "open" },
      ],
      { statuses: STATUSES, labels: LABELS },
    );
    assert.deepEqual(ids(open), ["z", "a"]);
  });
});
