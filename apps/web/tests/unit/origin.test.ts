/**
 * "Where did this detour start?" (`$lib/core/origin.ts`, #408).
 *
 * Two halves worth pinning. The **shape** of what it produces, because the consumer is a
 * `redirect(303, …)` on the server and a `goto()` in the browser, and neither can tell a
 * malformed return address from a hostile one. And the **fallback**, because absence is what
 * keeps every screen that was never part of a detour behaving exactly as it did: a `?from=`
 * that `safeInternalPath` refuses must read as *no origin at all*, never as an error — a stale
 * link is not the user's mistake.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { fromHref, originOf, withOrigin } from "../../src/lib/core/origin.ts";

const at = (href: string) => new URL(href, "https://bureau.schakl.test");

describe("fromHref states where the visitor is standing", () => {
  test("appends the encoded origin to a bare path", () => {
    assert.equal(
      fromHref("/contacts/k1", at("/companies/c1")),
      "/contacts/k1?from=%2Fcompanies%2Fc1",
    );
  });

  test("keeps the origin's own query, which is the screen they were on", () => {
    // `core/screen-position` restores a scroll offset only for the URL it recognises as the one
    // that was left, so dropping the query would return them to the top of a different slice.
    assert.equal(
      fromHref("/tasks/t1", at("/companies/c1?tab=work&page=2")),
      "/tasks/t1?from=%2Fcompanies%2Fc1%3Ftab%3Dwork%26page%3D2",
    );
  });

  test("joins a target that already carries a query", () => {
    assert.equal(
      fromHref("/contacts/k1?edit=1", at("/companies/c1")),
      "/contacts/k1?edit=1&from=%2Fcompanies%2Fc1",
    );
  });

  test("a string origin is taken as given", () => {
    assert.equal(fromHref("/domains/d1", "/companies/c1"), "/domains/d1?from=%2Fcompanies%2Fc1");
  });

  test("refuses to produce a return address a browser would read as another origin", () => {
    assert.equal(fromHref("/domains/d1", "//evil.example"), "/domains/d1");
    assert.equal(fromHref("/domains/d1", "https://evil.example"), "/domains/d1");
  });
});

describe("originOf reads it back, or answers nothing at all", () => {
  test("an ordinary internal path comes back whole", () => {
    assert.equal(originOf(at("/contacts/k1?from=%2Fcompanies%2Fc1")), "/companies/c1");
    assert.equal(
      originOf(at("/tasks/t1?from=%2Fcompanies%2Fc1%3Ftab%3Dwork")),
      "/companies/c1?tab=work",
    );
  });

  test("no marker is no origin — which is every screen that was never a detour", () => {
    assert.equal(originOf(at("/contacts/k1")), null);
    assert.equal(originOf(at("/contacts/k1?edit=1")), null);
  });

  test("a hostile or malformed value falls back rather than raising", () => {
    // A `?from=` travels in a URL anyone can write; `redirect.ts` settles the rules and this
    // must not re-answer them. Everything it refuses reads as "nobody came from anywhere".
    assert.equal(originOf(at("/contacts/k1?from=https%3A%2F%2Fevil.example")), null);
    assert.equal(originOf(at("/contacts/k1?from=%2F%2Fevil.example")), null);
    assert.equal(originOf(at("/contacts/k1?from=%2F%5Cevil.example")), null);
    assert.equal(originOf(at("/contacts/k1?from=companies")), null);
    assert.equal(originOf(at("/contacts/k1?from=%2Fcompanies%00")), null);
  });
});

describe("withOrigin carries it onto a form action", () => {
  test("a delete action names its action and keeps the origin", () => {
    // A browser resolves `?/delete` against the current URL, which replaces the whole query
    // string — so without this the origin is dropped at exactly the moment the server needs it.
    assert.equal(
      withOrigin("?/delete", at("/contacts/k1?from=%2Fcompanies%2Fc1")),
      "?/delete&from=%2Fcompanies%2Fc1",
    );
  });

  test("no origin leaves the action exactly as it was", () => {
    assert.equal(withOrigin("?/delete", at("/contacts/k1")), "?/delete");
    assert.equal(
      withOrigin("?/deleteProject", at("/projects/p1?from=nonsense")),
      "?/deleteProject",
    );
  });
});
