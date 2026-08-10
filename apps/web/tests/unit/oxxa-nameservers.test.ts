/**
 * `sameNameservers()` is what decides whether the registrar panel asks for a change (#296).
 *
 * The panel used to head a form "Nameservers wijzigen bij OXXA", pre-fill it with Cloudflare's
 * pair, and show it over a register that was already holding exactly that pair — an outstanding
 * action where there was none, on the most common finished state this integration has. The whole
 * fix rests on comparing two delegations the way DNS reads them, and every way of getting that
 * wrong is silent: order sensitivity calls an unchanged domain changed, and treating "nothing
 * known" as agreement makes the panel fall quiet about a delegation it has not read yet.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { parseNameservers, sameNameservers } from "../../src/lib/modules/oxxa/types.ts";

const CLOUDFLARE = ["ana.ns.cloudflare.com", "bob.ns.cloudflare.com"];

describe("sameNameservers", () => {
  test("order is not part of a delegation", () => {
    assert.equal(sameNameservers(CLOUDFLARE, [...CLOUDFLARE].reverse()), true);
  });

  test("case and the root dot are not either — a registrar returns what it stores", () => {
    assert.equal(
      sameNameservers(["ANA.NS.Cloudflare.com.", "bob.ns.cloudflare.com."], CLOUDFLARE),
      true,
    );
  });

  test("a different pair is a different delegation", () => {
    assert.equal(sameNameservers(["ns1.oud.nl", "ns2.oud.nl"], CLOUDFLARE), false);
  });

  test("a superset is not a match: the third nameserver is a real difference", () => {
    assert.equal(sameNameservers([...CLOUDFLARE, "ns3.elders.nl"], CLOUDFLARE), false);
  });

  test("nothing known never agrees with something known, in either direction", () => {
    // The panel asks this question before any Cloudflare zone exists, and on a domain whose
    // register has never been read. Both must read as "no answer", never as "already fine".
    assert.equal(sameNameservers([], CLOUDFLARE), false);
    assert.equal(sameNameservers(CLOUDFLARE, []), false);
    assert.equal(sameNameservers(null, null), false);
    assert.equal(sameNameservers(undefined, CLOUDFLARE), false);
  });

  test("what the textarea holds compares against the suggestion it was seeded from", () => {
    // The "use Cloudflare's nameservers" button hides itself on exactly this comparison, so the
    // two sides really are a parsed box and a panel's list.
    assert.equal(
      sameNameservers(
        parseNameservers("ANA.ns.cloudflare.com\nbob.ns.cloudflare.com\n"),
        CLOUDFLARE,
      ),
      true,
    );
    assert.equal(sameNameservers(parseNameservers("ana.ns.cloudflare.com"), CLOUDFLARE), false);
  });
});
