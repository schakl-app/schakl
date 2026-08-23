/**
 * The client hub's fixed layout (`$lib/modules/companies/hub.ts`, #403).
 *
 * The bug this pins is invisible on any one client: every card renders, every panel holds the
 * right data, and only the *width and reading position* differ — and only against a client whose
 * empty panels are a different set from the one the developer was looking at. So the property is
 * asserted across several clients at once rather than measured in a browser against one.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { arrangePanels, type HubPanel, type HubRow } from "../../src/lib/modules/companies/hub.ts";

const NONE: ReadonlySet<string> = new Set();

/** A panel as the API declares it: `half` unless said otherwise, present unless said empty. */
function panel(key: string, size: "half" | "full" = "half", empty = false): HubPanel {
  return { key, size, empty };
}

/**
 * The working surfaces the hub actually composes, in `position` order (#403 changed prominence,
 * not position, so this is the primary lane as it ships): Gegevens 10, Contactpersonen 20,
 * Projecten 25, Taken 30, Contactmomenten 35, Uren 40, Marketing 50.
 */
const PRIMARY: HubPanel[] = [
  panel("companies.details"),
  panel("contacts.company"),
  panel("projects.company"),
  panel("tasks.company", "full"),
  panel("interactions.company", "full"),
  panel("time.company"),
  panel("marketing.overview", "full"),
];

/** The register lane as it ships, so the rules are asserted against both lanes. */
const REGISTERS: HubPanel[] = [
  panel("google_ads.company"),
  panel("google_tag_manager.company"),
  panel("reporting.reports"),
  panel("google.drive.company"),
  panel("subscriptions.company"),
  panel("timeon.company", "full"),
  panel("invoicing.company"),
  panel("websites.company"),
  panel("domains.company"),
  panel("activity.trail"),
  panel("snelstart.company", "full"),
  panel("uptime.company"),
];

/** `key -> {kind, lane, index}` — everything a reader can see about where a card was put. */
function placements(rows: HubRow<HubPanel>[]) {
  const seen: Record<string, { kind: string; lane: number; index: number }> = {};
  rows.forEach((row, index) => {
    if (row.kind === "full") {
      seen[row.panel.key] = { kind: "full", lane: -1, index };
      return;
    }
    row.lanes.forEach((lane, laneIndex) => {
      for (const { panel: p } of lane) seen[p.key] = { kind: "half", lane: laneIndex, index };
    });
  });
  return seen;
}

/** The same client, with some of its panels empty. */
function withEmpty(panels: HubPanel[], ...keys: string[]): HubPanel[] {
  return panels.map((p) => (keys.includes(p.key) ? { ...p, empty: true } : p));
}

describe("a panel is drawn at the width it declared", () => {
  test("a half with no half neighbour is still a half", () => {
    // Uren (40) sits between two full-width panels, so under #364's run rule it was always alone
    // on its run and therefore always drawn full width — 992 px and 509 px tall on every client
    // with hours, which is the "grote blokken met geboekte uren" the team named.
    const rows = arrangePanels(PRIMARY, NONE);
    const uren = placements(rows)["time.company"];
    assert.equal(uren.kind, "half");
    assert.equal(uren.lane, 0);
  });

  test("no half is ever promoted to a full row, and no full is ever demoted", () => {
    for (const panels of [PRIMARY, withEmpty(PRIMARY, "projects.company", "time.company")]) {
      const seen = placements(arrangePanels(panels, NONE));
      for (const p of panels) {
        if (p.empty) continue;
        assert.equal(seen[p.key].kind, p.size, `${p.key} was drawn as a ${seen[p.key].kind}`);
      }
    }
  });
});

describe("the same panel lands in the same place on every client", () => {
  // The three clients from the issue's measurements, sketched by what they lack.
  const clients: Record<string, HubPanel[]> = {
    everything: PRIMARY,
    // No hours logged yet.
    bakkerij: withEmpty(PRIMARY, "time.company"),
    // No projects, no hours, nothing on file from marketing.
    atelier: withEmpty(PRIMARY, "projects.company", "time.company", "marketing.overview"),
    // A brand-new client: one contact person and nothing else.
    fresh: withEmpty(
      PRIMARY,
      "projects.company",
      "tasks.company",
      "interactions.company",
      "time.company",
      "marketing.overview",
    ),
  };

  test("width and lane do not depend on which neighbours are empty", () => {
    const reference = placements(arrangePanels(PRIMARY, NONE));
    for (const [name, panels] of Object.entries(clients)) {
      const seen = placements(arrangePanels(panels, NONE));
      for (const [key, place] of Object.entries(seen)) {
        assert.equal(place.kind, reference[key].kind, `${key} changed width on ${name}`);
        assert.equal(place.lane, reference[key].lane, `${key} changed lane on ${name}`);
      }
    }
  });

  test("Contactpersonen is in the same lane on all four", () => {
    // The measured symptom: 488 px on two clients and 992 px on a third.
    for (const [name, panels] of Object.entries(clients)) {
      const contacts = placements(arrangePanels(panels, NONE))["contacts.company"];
      assert.deepEqual(
        { kind: contacts.kind, lane: contacts.lane },
        { kind: "half", lane: 1 },
        name,
      );
    }
  });

  test("a folded full-width panel still ends the run of halves around it", () => {
    // Otherwise Contactmomenten going empty would weld Projecten's run onto Uren's and re-seat
    // both — a run boundary that moves is the whole bug, one level down.
    const seen = placements(arrangePanels(withEmpty(PRIMARY, "interactions.company"), NONE));
    assert.equal(seen["time.company"].lane, 0);
    assert.equal(seen["companies.details"].lane, 0);
    assert.equal(seen["contacts.company"].lane, 1);
  });

  test("unfolding an empty panel from the ＋ strip moves nothing", () => {
    const panels = withEmpty(PRIMARY, "projects.company");
    const folded = placements(arrangePanels(panels, NONE));
    const unfolded = placements(arrangePanels(panels, new Set(["projects.company"])));
    for (const [key, place] of Object.entries(folded)) {
      assert.deepEqual(unfolded[key], place, `${key} moved when Projecten was unfolded`);
    }
    assert.deepEqual(unfolded["projects.company"], { kind: "half", lane: 0, index: 0 });
  });
});

describe("reading order", () => {
  test("each lane reads top to bottom", () => {
    for (const rows of [arrangePanels(PRIMARY, NONE), arrangePanels(REGISTERS, NONE)]) {
      for (const row of rows) {
        if (row.kind !== "lanes") continue;
        for (const lane of row.lanes) {
          const seats = lane.map((s) => s.seat);
          assert.deepEqual(
            seats,
            [...seats].sort((a, b) => a - b),
          );
        }
      }
    }
  });

  test("halves alternate lanes in declared order", () => {
    const rows = arrangePanels([panel("a"), panel("b"), panel("c"), panel("d")], NONE);
    assert.equal(rows.length, 1);
    assert.equal(rows[0].kind, "lanes");
    if (rows[0].kind !== "lanes") return;
    assert.deepEqual(
      rows[0].lanes.map((lane) => lane.map((s) => s.panel.key)),
      [
        ["a", "c"],
        ["b", "d"],
      ],
    );
  });

  test("the seat is the mobile order, so one column reads in declared order", () => {
    const rows = arrangePanels([panel("a"), panel("b"), panel("c")], NONE);
    if (rows[0].kind !== "lanes") throw new Error("expected a lanes row");
    const flat = rows[0].lanes.flat().sort((x, y) => x.seat - y.seat);
    assert.deepEqual(
      flat.map((s) => s.panel.key),
      ["a", "b", "c"],
    );
  });
});

describe("nothing is drawn for what is not there", () => {
  test("a run whose halves have all folded draws no row at all", () => {
    const rows = arrangePanels(
      [panel("a", "half", true), panel("b", "half", true), panel("c", "full")],
      NONE,
    );
    assert.deepEqual(
      rows.map((r) => r.key),
      ["c"],
    );
  });

  test("an empty lane keeps its column so the other lane does not slide left", () => {
    // Gegevens (seat 0) and Projecten (seat 2) are lane 0; with both empty, Contactpersonen must
    // stay in lane 1 rather than becoming the only card and moving to the left column.
    const rows = arrangePanels(withEmpty(PRIMARY, "companies.details", "projects.company"), NONE);
    if (rows[0].kind !== "lanes") throw new Error("expected a lanes row");
    assert.deepEqual(rows[0].lanes[0], []);
    assert.deepEqual(
      rows[0].lanes[1].map((s) => s.panel.key),
      ["contacts.company"],
    );
  });

  test("every panel folded means no rows", () => {
    assert.deepEqual(arrangePanels(withEmpty(PRIMARY, ...PRIMARY.map((p) => p.key)), NONE), []);
  });
});
