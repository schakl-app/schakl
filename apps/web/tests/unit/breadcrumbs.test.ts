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
  parentRules,
  RECORD_TYPES,
  routeParamNames,
  statedAncestor,
  type CrumbLink,
  type ParentRule,
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

  test("a task created from a client keeps the client", () => {
    // The return trip out of create-then-edit (#402). A task made from the client hub carries
    // `company_id` and usually no project at all, so the crumb row is the *only* thing that
    // brings the user back — and it is one `PARENT_FK` entry away from silently going away.
    const task = pageRecord({ task: { id: "t1", title: "Bellen", company_id: "c1" } }, echo)!;
    assert.equal(isParentOf(acme, task), true);
  });

  test("a task made from someone else's client does not draw this one", () => {
    const task = pageRecord({ task: { id: "t1", title: "Bellen", company_id: "c9" } }, echo)!;
    assert.equal(isParentOf(acme, task), false);
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

describe("a client is confirmed through a collection as well as through a column", () => {
  // The bug (#401): a contact belongs to its clients through `company_contacts`, so `ContactRead`
  // answers with a list and carries no `company_id`. Every other record confirmed its client from
  // a column, so the mechanism looked complete and failed on exactly one entity — the crumb row
  // reset and "up" from a client's contact person became the org-wide address book.
  const marieke = (companies: { company_id: string }[]) =>
    pageRecord({ contact: { id: "k1", first_name: "Marieke", companies } }, echo)!;

  test("a contact opened from one of its own clients keeps the client", () => {
    assert.equal(isParentOf(acme, marieke([{ company_id: "c1" }])), true);
  });

  test("a contact of several clients keeps the one you came from", () => {
    const record = marieke([{ company_id: "c7" }, { company_id: "c1" }]);
    assert.equal(isParentOf(acme, record), true);
  });

  test("a contact opened from someone else's client does not", () => {
    assert.equal(isParentOf(acme, marieke([{ company_id: "c7" }])), false);
  });

  test("a contact attached to nobody does not", () => {
    assert.equal(isParentOf(acme, marieke([])), false);
  });

  test("a collection rule only ever confirms its own ancestor type", () => {
    // `companies` says nothing about the project you happened to walk through a moment ago.
    assert.equal(isParentOf(site, marieke([{ company_id: "c1" }])), false);
  });
});

describe("a stated ancestor outranks an inferred one, and is confirmed the same way", () => {
  // #408. The navigation-order trail lives in `sessionStorage`, so a reload, a new tab and the
  // page after a `redirect(303, …)` have never had a crumb. A `?from=` says the ancestor outright
  // — and says only a *path*, never a label, because the label is display text and the URL is
  // written by whoever is holding the keyboard.
  const at = (href: string) => new URL(href, "https://bureau.schakl.test");
  const marieke = (companies: { company_id: string; name: string }[]) =>
    pageRecord({ contact: { id: "k1", first_name: "Marieke", companies } }, echo)!;

  test("a contact opened from one of its own clients names it", () => {
    const crumb = statedAncestor(
      at("/contacts/k1?from=%2Fcompanies%2Fc1"),
      marieke([{ company_id: "c1", name: "Bakkerij Van Loon" }]),
    );
    assert.deepEqual(crumb, {
      type: "company",
      id: "c1",
      label: "Bakkerij Van Loon",
      href: "/companies/c1",
    });
  });

  test("a `?from=` naming a client the record is not linked to is not a crumb", () => {
    // Otherwise a hand-written URL would become a hierarchy — the one safety property the
    // inferred trail already has, and the one a stated ancestor must not be allowed to skip.
    const crumb = statedAncestor(
      at("/contacts/k1?from=%2Fcompanies%2Fc9"),
      marieke([{ company_id: "c1", name: "Bakkerij Van Loon" }]),
    );
    assert.equal(crumb, null);
  });

  test("the origin's own query is ignored when reading which record it names", () => {
    const crumb = statedAncestor(
      at("/contacts/k1?from=%2Fcompanies%2Fc1%3Ftab%3Dwork"),
      marieke([{ company_id: "c1", name: "Bakkerij Van Loon" }]),
    );
    assert.equal(crumb?.href, "/companies/c1");
  });

  test("a section the trail knows no parent rule for states nothing", () => {
    // Still a perfectly good return destination — the two questions are separate, and only the
    // crumb needs confirming.
    const crumb = statedAncestor(
      at("/contacts/k1?from=%2Finstellingen"),
      marieke([{ company_id: "c1", name: "Bakkerij Van Loon" }]),
    );
    assert.equal(crumb, null);
  });

  test("a record that confirms an ancestor it cannot name draws no crumb without a lookup", () => {
    // A task carries `company_id` and no client name. The lookup is what the page's own load
    // already holds; without one the honest answer is no crumb rather than an invented label.
    const task = pageRecord({ task: { id: "t1", title: "Fix", company_id: "c1" } }, echo)!;
    const url = at("/tasks/t1?from=%2Fcompanies%2Fc1");
    assert.equal(statedAncestor(url, task), null);
    assert.equal(statedAncestor(url, task, () => "Bakkerij Van Loon")?.label, "Bakkerij Van Loon");
  });

  test("a record's own name for the ancestor wins over the lookup", () => {
    const project = pageRecord(
      { project: { id: "p1", name: "Site", company_id: "c1", company_name: "Bakkerij Van Loon" } },
      echo,
    )!;
    const crumb = statedAncestor(
      at("/projects/p1?from=%2Fcompanies%2Fc1"),
      project,
      () => "Anders",
    );
    assert.equal(crumb?.label, "Bakkerij Van Loon");
  });
});

/**
 * The third half: every record the crumb row can be about is asked whether it *can* confirm a
 * client, against the generated API types rather than against anybody's memory (#401).
 *
 * This is CLAUDE.md §15's "failure mode (1) — no anchor" one layer out. A model whose client link
 * is indirect declares `__company_horizon_clause__` on the server for exactly that reason; the
 * trail needs the matching rule, and the way that went wrong was invisible — `record["company_id"]`
 * on a record that has no such column is `undefined`, which reads as "not this client" and never
 * as "this record cannot answer the question".
 *
 * A new detail page therefore has to be listed here: either with the endpoint whose response the
 * sweep checks, or with a stated reason why no client is involved at all.
 */
const DETAIL_ENDPOINT: Record<string, string | null> = {
  // The client itself — an ancestor, never a child of one.
  company: null,
  contact: "/api/v1/contacts/{contact_id}",
  project: "/api/v1/projects/{project_id}",
  task: "/api/v1/tasks/{task_id}",
  domain: "/api/v1/domains/{domain_id}",
  website: "/api/v1/websites/{website_id}",
  invoice: "/api/v1/invoicing/invoices/{invoice_id}",
  quote: "/api/v1/invoicing/quotes/{quote_id}",
  report: "/api/v1/reporting/reports/{report_id}",
  ads_account: "/api/v1/google-ads/accounts/{account_id}",
  // Org-wide or instance-level: no client hub leads here, so no client crumb to confirm.
  org: null,
  automation_rule: null,
  role: null,
};

const SCHEMA = readFileSync(join(here, "../../src/lib/core/api/schema.d.ts"), "utf8");

/** The block of a named `interface`-level entry, matched by its own indentation. */
function block(indent: number, name: string, within = SCHEMA): string | null {
  const pad = " ".repeat(indent);
  const start = within.indexOf(`\n${pad}${name}: {\n`);
  if (start < 0) return null;
  const end = within.indexOf(`\n${pad}};`, start);
  return end < 0 ? null : within.slice(start, end);
}

/** The schema name a `GET <path>` answers with, walked path → operation → 200 response. */
function responseSchema(path: string): string | null {
  const pathBlock = block(4, JSON.stringify(path));
  const operation = pathBlock?.match(/\n {8}get: operations\["([^"]+)"\]/)?.[1];
  if (!operation) return null;
  const opBlock = block(4, operation);
  return opBlock?.match(/200: \{[\s\S]*?components\["schemas"\]\["(\w+)"\]/)?.[1] ?? null;
}

/** One property line of a schema, at the single indentation its own properties sit on. */
function property(schema: string, name: string): string | null {
  const body = block(8, schema);
  const line = body?.match(new RegExp(`^ {12}${name}\\??: (.+)$`, "m"))?.[1];
  return line ?? null;
}

/** Whether a record answering with this schema could ever satisfy the rule. */
function satisfiable(rule: ParentRule, schema: string): boolean {
  if (!("collection" in rule)) return property(schema, rule.fk) !== null;
  const item = property(schema, rule.collection)?.match(
    /components\["schemas"\]\["(\w+)"\]\[\]/,
  )?.[1];
  return Boolean(item && property(item, rule.fk) !== null);
}

describe("every record the crumb row can be about can confirm its client", () => {
  test("the sweep can read the generated schema", () => {
    // A green run over an unparseable file would assert nothing at all.
    assert.equal(responseSchema("/api/v1/contacts/{contact_id}"), "ContactRead");
    assert.ok(property("ContactRead", "companies"), "ContactRead has no companies list");
    assert.equal(property("ContactRead", "company_id"), null);
  });

  for (const type of RECORD_TYPES) {
    test(type, () => {
      assert.ok(
        type in DETAIL_ENDPOINT,
        `"${type}" is a record the crumb row can be about and nothing says whether it hangs off ` +
          "a client. Add it to DETAIL_ENDPOINT with its detail endpoint, or with null and a " +
          "comment saying why no client is involved.",
      );
      const path = DETAIL_ENDPOINT[type];
      if (!path) return;
      const schema = responseSchema(path);
      assert.ok(schema, `no 200 response schema found for GET ${path}`);
      assert.ok(
        parentRules("company").some((rule) => satisfiable(rule, schema!)),
        `${schema} (GET ${path}) names its client in no way PARENT_RULES.company can read, so a ` +
          `${type} opened from a client's page would drop the client from the crumb row. Either ` +
          "the API owes it a company reference, or the trail owes it a rule (#401).",
      );
    });
  }
});
