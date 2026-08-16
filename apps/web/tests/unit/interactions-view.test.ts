/**
 * Which view `/interactions` opens on.
 *
 * The page is now two views over one list — the review queue and the whole timeline — and the
 * queue is the default. That is exactly the kind of decision a test has to hold: nothing breaks
 * when it flips, the screen simply stops being a queue, every functional check still passes, and
 * the only person who would notice is one who already knew it used to open there.
 *
 * The record carve-out is the half that is easiest to lose. A panel's "8 van 137" links here with
 * `?company_id=…`, and a client's timeline is not somebody's inbox — defaulting *that* to the
 * queue would answer 0 under a notice that had just said 137, which is the bug #323 exists to
 * have fixed, one filter over.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { interactionView, scopedRecords } from "../../src/lib/modules/interactions/scope.ts";

const COMPANY = "11111111-1111-1111-1111-111111111111";

const view = (query: string) => interactionView(new URLSearchParams(query));

describe("interactionView", () => {
  test("the plain list opens on the review queue", () => {
    assert.equal(view(""), "pending");
    assert.equal(view("q=offerte&sort=-occurred_at&page=3"), "pending");
  });

  test("both tabs are linkable, and `all` is the one that widens", () => {
    assert.equal(view("status=pending"), "pending");
    assert.equal(view("status=all"), "all");
  });

  test("a record-scoped link opens on everything, not on nought unreviewed e-mails", () => {
    assert.equal(view(`company_id=${COMPANY}`), "all");
    assert.equal(view(`project_id=${COMPANY}&include=tasks`), "all");
    // …and it is still only a default: the tab wins where it was pressed.
    assert.equal(view(`company_id=${COMPANY}&status=pending`), "pending");
  });

  test("a record the load would throw away does not silently widen the list", () => {
    // A non-uuid is dropped rather than 422'd, so the page draws no chip — and a view rule that
    // believed it would open the firehose with nothing on screen saying why.
    assert.equal(view("company_id=not-a-uuid"), "pending");
    assert.equal(scopedRecords(new URLSearchParams("company_id=not-a-uuid")).length, 0);
  });

  test("an unknown token falls back rather than answering nothing", () => {
    // These arrive from a query string anyone can edit; a stale bookmark gets the whole list.
    assert.equal(view("status=logged"), "all");
    assert.equal(view("status="), "all");
  });
});

describe("scopedRecords", () => {
  test("reads the four record filters in chip order and keeps only real ids", () => {
    const found = scopedRecords(
      new URLSearchParams(`contact_id=${COMPANY}&company_id=${COMPANY}&task_id=nope`),
    );
    assert.deepEqual(
      found.map((r) => r.field),
      ["company_id", "contact_id"],
    );
  });
});
