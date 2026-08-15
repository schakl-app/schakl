/**
 * Which picker options are offered, and which are merely findable.
 *
 * The same shape as `time-task-picker.test.ts` and for the same reason: none of these rules is
 * visible on screen. A dropdown that lists every client the agency ever archived looks perfectly
 * fine — the cost arrives months later as a domain attached to a company nobody works for, or an
 * invoice addressed to a relationship that ended in 2023. So the four rules are pinned here:
 *
 *   1. a retired row is out of the opening list, not out of the picker;
 *   2. a row already picked is offered whatever it became — the field must be able to say what
 *      is in it;
 *   3. a live-but-not-ordinary status is *named* rather than silently mixed in;
 *   4. an unknown or absent status means "don't know", which keeps the option on offer.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { splitLifecycle } from "../../src/lib/core/picker.ts";

/** The companies module's own vocabulary, spelled out rather than imported: `$lib` does not
 *  resolve under node's runner, and restating it is also what makes a change to it visible. */
const CLIENTS = {
  retired: ["archived"],
  quiet: ["active"],
  statusLabel: (status: string) => ({ lead: "Lead", archived: "Gearchiveerd" })[status] ?? status,
};

const ROWS = [
  { value: "a", label: "Actief Bureau", status: "active" },
  { value: "b", label: "Bakkerij", status: "lead" },
  { value: "c", label: "Eiland", status: "archived" },
];

describe("splitLifecycle", () => {
  test("retires only the named statuses and keeps the rest on offer", () => {
    const { live, retired } = splitLifecycle(ROWS, CLIENTS);
    assert.deepEqual(
      live.map((o) => o.value),
      ["a", "b"],
    );
    assert.deepEqual(
      retired.map((o) => o.value),
      ["c"],
    );
  });

  test("names every status except the quiet one", () => {
    const { live, retired } = splitLifecycle(ROWS, CLIENTS);
    assert.equal(live[0].hint, undefined, "the ordinary status says nothing");
    assert.equal(live[1].hint, "Lead", "a live but non-ordinary status is said out loud");
    assert.equal(
      retired[0].hint,
      "Gearchiveerd",
      "and so is a retired one — that is how it is found",
    );
  });

  test("the status leads the module's own hint rather than replacing it", () => {
    const { live } = splitLifecycle(
      [{ value: "b", label: "Bakkerij", status: "lead", hint: "0002" }],
      CLIENTS,
    );
    assert.equal(live[0].hint, "Lead · 0002");
  });

  test("a picked row stays on offer whatever it became", () => {
    const { live, retired } = splitLifecycle(ROWS, { ...CLIENTS, selectedId: "c" });
    assert.deepEqual(
      live.map((o) => o.value),
      ["a", "b", "c"],
    );
    assert.deepEqual(retired, [], "nothing is hidden from a field that is holding it");
    assert.equal(live[2].hint, "Gearchiveerd", "and it still says what it is");
  });

  test("several selections are honoured — one lookup often feeds two controls", () => {
    const rows = [...ROWS, { value: "d", label: "Fietsen", status: "archived" }];
    const { live, retired } = splitLifecycle(rows, { ...CLIENTS, selectedId: ["", "d", null] });
    assert.deepEqual(
      live.map((o) => o.value),
      ["a", "b", "d"],
    );
    assert.deepEqual(
      retired.map((o) => o.value),
      ["c"],
      "an empty or null selection narrows nothing",
    );
  });

  test("an unknown or absent status is not a retirement", () => {
    const { live, retired } = splitLifecycle(
      [
        { value: "x", label: "Geen status" },
        { value: "y", label: "Onbekend", status: "zombie" },
      ],
      CLIENTS,
    );
    assert.deepEqual(
      live.map((o) => o.value),
      ["x", "y"],
    );
    assert.deepEqual(retired, []);
    assert.equal(live[0].hint, undefined, "absent means nothing to say, not an empty label");
    assert.equal(live[1].hint, "zombie", "and an unrecognised one is passed to the labeller as-is");
  });

  test("order is preserved — the caller already sorted the lookup", () => {
    const rows = [
      { value: "z", label: "Zonnehuis", status: "active" },
      { value: "a", label: "Aanbouw", status: "active" },
    ];
    assert.deepEqual(
      splitLifecycle(rows, CLIENTS).live.map((o) => o.label),
      ["Zonnehuis", "Aanbouw"],
    );
  });
});
