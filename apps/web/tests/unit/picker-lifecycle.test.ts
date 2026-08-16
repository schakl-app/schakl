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

/**
 * The employees' half of the same rule.
 *
 * `$lib/core/members` cannot be imported here — it reaches `$lib/core/i18n`, which reaches
 * Paraglide's generated output, and `$lib` does not resolve under node's runner — so its
 * vocabulary is restated, exactly as `CLIENTS` above is. That restatement is the point: the
 * numbers below are what the app must keep doing, and a change to the rule has to come here
 * and disagree with them out loud.
 *
 * Why it needs pinning at all: an account is the one lookup with *no* status column. It has a
 * boolean, `is_active`, and every picker in the app rendered the roster flat because a flat
 * list of names is exactly what a correct one looks like. The cost shows up weeks later as a
 * task assigned to somebody who cannot sign in to see it.
 */
const STAFF = {
  retired: ["inactive"],
  quiet: ["active"],
  statusLabel: (status: string) =>
    ({ inactive: "Gedeactiveerd", active: "Actief" })[status] ?? status,
};

/** `memberStatus`, restated: one bit, two words. */
const asOption = (m: { user_id: string; name: string; is_active?: boolean }) => ({
  value: m.user_id,
  label: m.name,
  status: m.is_active === false ? "inactive" : "active",
});

describe("splitLifecycle over members", () => {
  const ROSTER = [
    { user_id: "u1", name: "Renzo" },
    { user_id: "u2", name: "Vertrokken", is_active: false },
    { user_id: "u3", name: "Stan", is_active: true },
  ];

  test("a deactivated colleague leaves the opening list and keeps its name", () => {
    const { live, retired } = splitLifecycle(ROSTER.map(asOption), STAFF);
    assert.deepEqual(
      live.map((o) => o.label),
      ["Renzo", "Stan"],
    );
    assert.deepEqual(
      retired.map((o) => o.label),
      ["Vertrokken"],
      "removed from the suggestions, not from the picker",
    );
    assert.equal(
      retired[0].hint,
      "Gedeactiveerd",
      "and it says so — dropping it from the list is only half the answer",
    );
    assert.equal(
      live[0].hint,
      undefined,
      "an active colleague is the ordinary case, said silently",
    );
  });

  test("whoever the field already holds is offered, deactivated or not", () => {
    const { live, retired } = splitLifecycle(ROSTER.map(asOption), { ...STAFF, selectedId: "u2" });
    assert.deepEqual(
      live.map((o) => o.label),
      ["Renzo", "Vertrokken", "Stan"],
      "a task assigned before they left must be able to say who holds it",
    );
    assert.deepEqual(retired, []);
    assert.equal(
      live[1].hint,
      "Gedeactiveerd",
      "still wearing its state — being offered is not being ordinary",
    );
  });

  test("a member whose flag never arrived is active", () => {
    const { live, retired } = splitLifecycle([asOption({ user_id: "u9", name: "Oud" })], STAFF);
    assert.deepEqual(
      live.map((o) => o.value),
      ["u9"],
      "an older payload must not retire the whole roster",
    );
    assert.deepEqual(retired, []);
  });
});
