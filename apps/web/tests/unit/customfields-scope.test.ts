/**
 * The browser's copy of the custom-field scope rule (`$lib/core/customfields/scope.ts`) has to
 * agree with `app/core/customfields/scoping.py`, because the form draws what the API will hold
 * the row to.
 */
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  applicableDefinitions,
  appliesTo,
  definitionScope,
} from "../../src/lib/core/customfields/scope.ts";

const unscoped = { config_json: { print_on_document: true } };
const byType = { config_json: { scope: { subscription_type_id: ["t1", "t2"] } } };
const byBoth = {
  config_json: { scope: { subscription_type_id: ["t1"], subscription_template_id: ["p1"] } },
};

describe("definitionScope", () => {
  it("normalises, folds duplicates and drops empty dimensions", () => {
    assert.deepEqual(
      definitionScope({
        config_json: {
          scope: { subscription_type_id: ["t1", "t1", ""], subscription_template_id: [] },
        },
      }),
      { subscription_type_id: ["t1"] },
    );
  });
  it("reads junk as unscoped", () => {
    assert.deepEqual(definitionScope({ config_json: { scope: "nonsense" } }), {});
    assert.deepEqual(definitionScope({ config_json: undefined }), {});
    assert.deepEqual(definitionScope({ config_json: { scope: { x: 42 } } }), {});
  });
});

describe("appliesTo", () => {
  it("an unscoped definition applies everywhere, and everything applies with no row scope", () => {
    assert.equal(appliesTo(unscoped, { subscription_type_id: null }), true);
    assert.equal(appliesTo(byType, null), true);
    assert.equal(appliesTo(byType, undefined), true);
  });
  it("matches on one dimension", () => {
    assert.equal(appliesTo(byType, { subscription_type_id: "t2" }), true);
    assert.equal(appliesTo(byType, { subscription_type_id: "t9" }), false);
  });
  it("is OR across dimensions", () => {
    assert.equal(
      appliesTo(byBoth, { subscription_type_id: "t9", subscription_template_id: "p1" }),
      true,
    );
    assert.equal(
      appliesTo(byBoth, { subscription_type_id: "t1", subscription_template_id: null }),
      true,
    );
    assert.equal(
      appliesTo(byBoth, { subscription_type_id: "t9", subscription_template_id: "p9" }),
      false,
    );
  });
  it("a null row value never matches", () => {
    assert.equal(appliesTo(byType, { subscription_type_id: null }), false);
    assert.equal(appliesTo(byType, {}), false);
  });
});

describe("applicableDefinitions", () => {
  it("keeps the unscoped and the matching ones, in order", () => {
    const defs = [byType, unscoped, byBoth];
    assert.deepEqual(applicableDefinitions(defs, { subscription_template_id: "p1" }), [
      unscoped,
      byBoth,
    ]);
  });
});
