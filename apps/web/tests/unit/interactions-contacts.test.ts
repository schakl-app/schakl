/**
 * The contact roster an interaction form picks from is scoped to the moment's client.
 *
 * This pins the half of the fix that is invisible on screen and trivial to reintroduce: the
 * *request* carries `company_id`, and an empty answer stays empty. The widen-to-the-org
 * fallback that used to live here looked like a kindness — an empty picker reads as broken —
 * and what it actually did was hand every client with no linked contacts the agency's whole
 * address book, so a contactmoment filed to that client could name a person at another one.
 * Nothing downstream cross-checks that, which is exactly why it needs a test rather than a
 * screen: both versions render a perfectly ordinary dropdown.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { afterEach, beforeEach, describe, test } from "node:test";

import { contactsForScope, forgetContacts } from "../../src/lib/modules/interactions/contacts.ts";

const ACME = "11111111-1111-1111-1111-111111111111";
const OTHER = "22222222-2222-2222-2222-222222222222";

/** Every contact in the org, as the unscoped list would answer. */
const EVERYONE = [
  { id: "c1", first_name: "Jan", last_name: "Jansen" },
  { id: "c2", first_name: "Piet", last_name: "Pietersen" },
];

let calls: string[] = [];
const realFetch = globalThis.fetch;

/** Answers `EVERYONE` unscoped and whatever `byScope` says for a `company_id=` request. */
function stubFetch(byScope: Record<string, unknown[]>) {
  globalThis.fetch = (async (input: string | URL | Request) => {
    const url = String(input);
    calls.push(url);
    const scoped = /[?&]company_id=([^&]+)/.exec(url);
    const items = scoped ? (byScope[scoped[1]] ?? []) : EVERYONE;
    return new Response(JSON.stringify({ items }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }) as typeof fetch;
}

beforeEach(() => {
  calls = [];
  forgetContacts();
});

afterEach(() => {
  globalThis.fetch = realFetch;
  forgetContacts();
});

describe("contactsForScope", () => {
  test("a client id is sent to the API as company_id", async () => {
    stubFetch({ [ACME]: [{ id: "c1", first_name: "Jan" }] });
    const items = await contactsForScope(ACME);

    assert.equal(calls.length, 1);
    assert.ok(calls[0].includes(`company_id=${ACME}`), calls[0]);
    assert.deepEqual(
      items.map((c) => c.id),
      ["c1"],
    );
  });

  test("an empty client roster stays empty — it never widens to the whole org", async () => {
    stubFetch({ [ACME]: [] });
    const items = await contactsForScope(ACME);

    assert.deepEqual(items, []);
    // One request, and it was the scoped one: no second, unscoped fetch behind it.
    assert.equal(calls.length, 1);
    assert.ok(calls[0].includes(`company_id=${ACME}`), calls[0]);
  });

  test("no client is the whole org, which is the only time everyone is right", async () => {
    stubFetch({});
    const items = await contactsForScope("");

    assert.equal(calls.length, 1);
    assert.ok(!calls[0].includes("company_id="), calls[0]);
    assert.deepEqual(
      items.map((c) => c.id),
      ["c1", "c2"],
    );
  });

  test("two clients get two rosters, not one shared answer", async () => {
    stubFetch({
      [ACME]: [{ id: "c1", first_name: "Jan" }],
      [OTHER]: [{ id: "c9", first_name: "Ada" }],
    });

    assert.deepEqual(
      (await contactsForScope(ACME)).map((c) => c.id),
      ["c1"],
    );
    assert.deepEqual(
      (await contactsForScope(OTHER)).map((c) => c.id),
      ["c9"],
    );
    assert.equal(calls.length, 2);
  });

  test("the same scope twice is one flight — forms on a page share it (#290)", async () => {
    stubFetch({ [ACME]: [{ id: "c1", first_name: "Jan" }] });
    const [a, b] = await Promise.all([contactsForScope(ACME), contactsForScope(ACME)]);

    assert.equal(calls.length, 1);
    assert.equal(a, b);
  });

  test("an inline create forgets the cache, so the next picker sees the new person", async () => {
    stubFetch({ [ACME]: [{ id: "c1", first_name: "Jan" }] });
    await contactsForScope(ACME);
    forgetContacts();

    stubFetch({
      [ACME]: [
        { id: "c1", first_name: "Jan" },
        { id: "c2", first_name: "Nieuw" },
      ],
    });
    assert.deepEqual(
      (await contactsForScope(ACME)).map((c) => c.id),
      ["c1", "c2"],
    );
  });

  test("a failed lookup is an empty roster, not a thrown form", async () => {
    globalThis.fetch = (async () => new Response("nope", { status: 500 })) as typeof fetch;
    assert.deepEqual(await contactsForScope(ACME), []);
  });
});
