/**
 * Every crumb the app can draw is a translated string, and a contextual trail only ever prints an
 * ancestor the record itself names.
 *
 * The first half is a sweep over the real route tree. The crumb row is rendered by the (app)
 * layout for every page, so a new screen gets one whether or not anyone thought about it — and
 * what "nobody thought about it" looked like was `prettify()`: the slug with a capital letter, in
 * English, on an app whose default language is Dutch. `/reports` read "Reports" and
 * `/companies/<id>/reporting` read "Reporting" for exactly that reason. Nothing in the build
 * noticed, because a prettified slug renders perfectly well.
 *
 * The second half is the rule that keeps the dynamic trail honest. History *suggests* an
 * ancestor; the record's own foreign key is what decides. Get that backwards and the row becomes
 * a back button claiming to be a hierarchy.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { describe, test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  isParentOf,
  literalLabelKey,
  pageRecord,
  routeParamNames,
  type CrumbLink,
} from "../../src/lib/core/breadcrumb-labels.ts";

const here = dirname(fileURLToPath(import.meta.url));
const ROUTES = join(here, "../../src/routes/(app)");
const MESSAGES = join(here, "../../../../messages");

const en = JSON.parse(readFileSync(join(MESSAGES, "en.json"), "utf8")) as Record<string, string>;
const nl = JSON.parse(readFileSync(join(MESSAGES, "nl.json"), "utf8")) as Record<string, string>;

/** Every URL path the (app) group serves, as its segments, with the route id beside it. */
function routes(dir = ROUTES, url: string[] = [], route: string[] = []): [string[], string][] {
  const found: [string[], string][] = [];
  const entries = readdirSync(dir, { withFileTypes: true });
  if (entries.some((entry) => entry.isFile() && entry.name === "+page.svelte")) {
    found.push([url, `/(app)/${route.join("/")}`]);
  }
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    // A group segment (`(app)`, `(cloud)`) is route structure, never a URL segment.
    const grouped = entry.name.startsWith("(") && entry.name.endsWith(")");
    found.push(
      ...routes(join(dir, entry.name), grouped ? url : [...url, entry.name], [
        ...route,
        entry.name,
      ]),
    );
  }
  return found;
}

const ALL = routes();

describe("every route segment the app ships has a translated crumb", () => {
  test("the sweep found the route tree", () => {
    // A green run over zero routes would assert nothing, and the failure mode (a moved directory)
    // is silent. 40 is comfortably under today's count and well above an accident.
    assert.ok(ALL.length > 40, `only ${ALL.length} routes found under ${ROUTES}`);
  });

  for (const [segments, routeId] of ALL) {
    if (segments.length === 0) continue; // the dashboard, whose single crumb is `nav.dashboard`
    const params = routeParamNames(routeId);
    test(`/${segments.join("/")}`, () => {
      segments.forEach((segment, index) => {
        // A parameter names itself from the record the page loaded, not from a message key.
        if (typeof params[index] === "string") return;
        const key = literalLabelKey(segments, index);
        assert.ok(
          key,
          `"${segment}" in /${segments.join("/")} has no crumb label — it would render as the ` +
            `bare slug "${segment}", in English, on a Dutch-default app. Add it to ROOTS, ` +
            "TAILS or TAILS_BY_ROOT in breadcrumb-labels.ts.",
        );
        assert.ok(en[key!], `${key} (for "${segment}") is missing from messages/en.json`);
        assert.ok(nl[key!], `${key} (for "${segment}") is missing from messages/nl.json`);
      });
    });
  }
});

describe("routeParamNames aligns the route id with the URL", () => {
  test("group segments are dropped so the indexes line up", () => {
    assert.deepEqual(routeParamNames("/(app)/companies/[id]/reporting"), [null, "id", null]);
  });

  test("a non-UUID parameter is still a parameter", () => {
    // The bug this replaced: an id-shaped guess printed a raw Google Ads customer number.
    assert.deepEqual(routeParamNames("/(app)/marketing/google-ads/[accountId]/policy"), [
      null,
      null,
      "accountId",
      null,
    ]);
  });

  test("no route id at all yields no opinion", () => {
    assert.deepEqual(routeParamNames(null), []);
  });
});

const echo = (key: string) => key;

describe("pageRecord names the record a page is about", () => {
  test("a website is the host it answers on", () => {
    const record = pageRecord({ website: { id: "w1", domain_name: "acme.nl", root: false } }, echo);
    assert.equal(record?.label, "www.acme.nl");
  });

  test("an unissued invoice falls back to its status rather than to nothing", () => {
    const record = pageRecord({ invoice: { id: "i1", number: null } }, echo);
    assert.equal(record?.label, "invoicing.status.draft");
  });

  test("a streamed promise under a record key is not a record", () => {
    // The Google Ads report page streams `report`; without the id guard its crumb read "…" and
    // the account beside it was never reached.
    const data = { report: Promise.resolve({}), account: { id: "a1", descriptive_name: "Acme" } };
    const record = pageRecord(data, echo);
    assert.equal(record?.type, "ads_account");
    assert.equal(record?.label, "Acme");
  });

  test("a list page is about no record", () => {
    assert.equal(pageRecord({ companies: [{ id: "c1", name: "Acme" }] }, echo), null);
  });
});

const acme: CrumbLink = { type: "company", id: "c1", label: "Acme", href: "/companies/c1" };
const site: CrumbLink = { type: "project", id: "p1", label: "Site", href: "/projects/p1" };

describe("a trail is drawn only over a link the record confirms", () => {
  test("a project opened from its own client keeps the client", () => {
    const project = pageRecord({ project: { id: "p1", name: "Site", company_id: "c1" } }, echo)!;
    assert.equal(isParentOf(acme, project), true);
  });

  test("a project opened from someone else's client does not", () => {
    const project = pageRecord({ project: { id: "p1", name: "Site", company_id: "c9" } }, echo)!;
    assert.equal(isParentOf(acme, project), false);
  });

  test("a task hangs off the project it came through", () => {
    const task = pageRecord({ task: { id: "t1", title: "Fix", project_id: "p1" } }, echo)!;
    assert.equal(isParentOf(site, task), true);
  });

  test("a record is never its own ancestor", () => {
    // `/companies/<id>/reporting` is still that company. Without this the row named it twice.
    const company = pageRecord({ company: { id: "c1", name: "Acme", company_id: "c1" } }, echo)!;
    assert.equal(isParentOf(acme, company), false);
  });

  test("an ancestor type with no foreign key on the record is refused", () => {
    const invoice = pageRecord({ invoice: { id: "i1", number: "2026-001" } }, echo)!;
    assert.equal(isParentOf(site, invoice), false);
  });
});
