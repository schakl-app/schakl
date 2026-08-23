/**
 * The visual system (#404, docs/UX.md § The visual system).
 *
 * Three of these four rules are the kind nothing else can catch. A state drawn in the tenant's
 * brand colour compiles, typechecks, renders and looks correct on the tenant the developer is
 * testing against — it only says the wrong thing on the *gold* one. A band heading quieter than
 * the panels inside it is two treatments that are each fine alone. And a card kind that stops
 * being distinguishable is a one-character edit in a class string. So they are asserted here,
 * against the source, which is what makes a rule a build break rather than a paragraph.
 *
 * Run with `pnpm web test:unit`.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  UI_STATES,
  stateChipClass,
  stateFillClass,
  stateFromTone,
  stateTextClass,
  type UiState,
} from "../../src/lib/core/state.ts";
import { burnBarClass, burnState, burnTextClass } from "../../src/lib/core/burn.ts";
import {
  BAND_HEADING,
  FIELD_LABEL,
  PAGE_TITLE,
  PANEL_HEADING,
} from "../../src/lib/core/ui/headings.ts";

const read = (relative: string) =>
  readFileSync(fileURLToPath(new URL(`../../${relative}`, import.meta.url)), "utf8");

const CLASS_GETTERS = [stateTextClass, stateChipClass, stateFillClass];

describe("the state palette is not the tenant's", () => {
  test("no state resolves to a brand or accent utility, at any variant", () => {
    // The whole point. `--brand-primary` is tenant data and is gold on a live instance, so a
    // state drawn in it is a different claim per tenant — and passes every test that is not
    // this one.
    for (const state of UI_STATES) {
      for (const getter of CLASS_GETTERS) {
        const classes = getter(state);
        assert.doesNotMatch(classes, /\bbrand\b/, `${getter.name}("${state}") = ${classes}`);
        assert.doesNotMatch(classes, /\baccent\b/, `${getter.name}("${state}") = ${classes}`);
      }
    }
  });

  test("burn draws from the palette, so a healthy budget is never the brand colour", () => {
    // The oldest breach: docs promised "green < 75 %" for years and the code drew `bg-brand`.
    for (const pct of [0, 40, 74.9, 75, 99.9, 100, 130]) {
      assert.doesNotMatch(burnBarClass(pct), /brand/, `burnBarClass(${pct})`);
    }
    assert.equal(burnState(40), "ok");
    assert.equal(burnState(80), "soon");
    assert.equal(burnState(130), "late");
    assert.equal(burnState(null), "neutral");
    // No budget means nothing to burn — an absent bar, not a green one.
    assert.equal(burnBarClass(null), "bg-transparent");
    assert.equal(burnTextClass(130), stateTextClass("late"));
    assert.equal(burnTextClass(40), "text-text");
  });

  test("every state but neutral carries a glyph, and neutral carries none", () => {
    // "Never colour alone": three of the five are adjacent hues by design, so the glyph is the
    // half that survives greyscale. `neutral` is the deliberate exception — a mark beside every
    // quiet figure spends the attention the palette exists to protect.
    //
    // Read as source rather than imported: the map holds lucide components, which are `.svelte`
    // files that node's runner cannot load. That is also why the map lives beside `StateMark`
    // and not in `state.ts` — see the note at the foot of that file.
    const icons = read("src/lib/core/ui/state-icons.ts");
    const map = /const ICON: Record<UiState, Component \| null> = \{([^}]*)\}/.exec(icons);
    assert.notEqual(map, null, "no ICON map in state-icons.ts");
    for (const state of UI_STATES) {
      const entry = new RegExp(`\\b${state}: ([A-Za-z]+)`).exec(map![1]);
      assert.notEqual(entry, null, `"${state}" is missing from the glyph map`);
      if (state === "neutral") assert.equal(entry![1], "null");
      else assert.notEqual(entry![1], "null", `"${state}" has no glyph`);
    }
  });

  test("the glyph map holds no colour of its own", () => {
    // A glyph that only reads because of the hue beside it is the rule not being kept, so the
    // map names icons and nothing else. Matched against class-shaped tokens rather than the
    // word: the file's own docblock says "--brand", which is the rule being explained.
    const icons = read("src/lib/core/ui/state-icons.ts");
    assert.doesNotMatch(icons, /\b(?:text|bg|border)-(?:red|amber|orange|emerald|green|brand)\b/);
  });

  test("every state resolves in both themes", () => {
    // A light-only shade goes near-black on a dark background — the failure mode 19 hand-written
    // reds in the app still have.
    for (const state of UI_STATES.filter((s) => s !== "neutral")) {
      for (const getter of CLASS_GETTERS) {
        assert.match(getter(state), /\bdark:/, `${getter.name}("${state}") has no dark variant`);
      }
    }
  });

  test("the API's tone vocabulary maps onto the palette, and an unknown tone is quiet", () => {
    assert.equal(stateFromTone("good"), "ok");
    assert.equal(stateFromTone("warn"), "soon");
    assert.equal(stateFromTone("bad"), "late");
    assert.equal(stateFromTone("neutral"), "neutral");
    // A tone this client does not recognise must never be louder than one it does.
    for (const unknown of [null, undefined, "", "critical", "info"]) {
      assert.equal(stateFromTone(unknown), "neutral", String(unknown));
    }
  });

  test("the five states are visually distinct from one another", () => {
    const seen = new Map<string, UiState>();
    for (const state of UI_STATES) {
      const key = stateTextClass(state);
      assert.equal(
        seen.get(key),
        undefined,
        `"${state}" draws the same text as "${seen.get(key)}"`,
      );
      seen.set(key, state);
    }
  });
});

describe("the heading scale is a ladder", () => {
  const size = (classes: string) => {
    const match = /\btext-(xs|sm|base|lg|xl)\b/.exec(classes);
    assert.notEqual(match, null, `no size in "${classes}"`);
    return ["xs", "sm", "base", "lg", "xl"].indexOf(match![1]);
  };

  test("a band heading outranks a panel heading, which outranks a field label", () => {
    // The inversion this exists to prevent: a container quieter than its own contents.
    assert.ok(size(PAGE_TITLE) > size(BAND_HEADING), "page title must outrank a band");
    assert.ok(size(BAND_HEADING) > size(PANEL_HEADING), "a band must outrank the panels in it");
    assert.ok(size(PANEL_HEADING) > size(FIELD_LABEL), "a panel must outrank its own fields");
  });

  test("only the quietest rung may be muted", () => {
    for (const rung of [PAGE_TITLE, BAND_HEADING, PANEL_HEADING]) {
      assert.doesNotMatch(rung, /text-text-muted/, `"${rung}" is muted`);
    }
    assert.match(FIELD_LABEL, /text-text-muted/);
  });

  test("no rung is uppercase — uppercase is not a level", () => {
    for (const rung of [PAGE_TITLE, BAND_HEADING, PANEL_HEADING, FIELD_LABEL]) {
      assert.doesNotMatch(rung, /\buppercase\b/, `"${rung}" is uppercase`);
    }
  });
});

describe("a card is not one thing", () => {
  const card = read("src/lib/core/ui/Card.svelte");
  const chrome = (kind: string) => {
    const match = new RegExp(`^\\s*${kind}: "([^"]+)"`, "m").exec(card);
    assert.notEqual(match, null, `no chrome declared for "${kind}"`);
    return match![1];
  };

  test("the four kinds are declared and none of them is a duplicate", () => {
    const kinds = ["stat", "panel", "register", "strip"];
    const drawn = kinds.map(chrome);
    assert.equal(new Set(drawn).size, kinds.length, `two kinds draw the same chrome: ${drawn}`);
  });

  test("a figure, a list and a reference block are distinguishable at a glance", () => {
    // Each carries a *different* signal, not a different value of one signal: the working
    // surface is the only bordered box, the figure is the only filled one, the reference is
    // neither and gets a rule instead.
    assert.match(chrome("panel"), /\bborder border-border\b/);
    assert.match(chrome("panel"), /\bbg-surface-raised\b/);

    assert.doesNotMatch(chrome("stat"), /\bborder\b/, "a stat card must carry no border");
    assert.match(chrome("stat"), /\bbg-surface-tint\b/);

    assert.doesNotMatch(chrome("register"), /\bbg-/, "a register must carry no fill");
    assert.match(chrome("register"), /\bborder-t\b/, "a register is a rule, not a box");

    assert.match(chrome("strip"), /\bborder-dashed\b/);
  });

  test("the tinted surface a stat card needs is a real token", () => {
    const css = read("src/app.css");
    assert.match(css, /--surface-tint:/, "--surface-tint is not declared");
    assert.match(css, /--color-surface-tint: var\(--surface-tint\)/, "not exposed to Tailwind");
  });

  test("the card draws the panel heading itself, so no caller can pick another size", () => {
    // The ladder is stated once. A `text-*` on a heading *here* would be a second answer, and
    // `Card` is the one component every panel in the app now goes through.
    assert.match(card, /PANEL_HEADING/);
    for (const heading of card.matchAll(/<h[23][^>]*>/g)) {
      assert.doesNotMatch(heading[0], /text-(xs|sm|base|lg|xl)\b/, heading[0]);
    }
  });
});

describe("the screens the issue measured", () => {
  test("no page still ships the 12px muted section heading it was measured on", () => {
    // Seven on the task page, one banding the hub's registers. The pattern survives elsewhere
    // (table headers, field labels over figures); what may not survive is a *card's own title*
    // or a *band* drawn this way, which is what these four files had.
    for (const file of [
      "src/routes/(app)/tasks/[id]/+page.svelte",
      "src/routes/(app)/companies/[id]/+page.svelte",
      "src/routes/(app)/tasks/+page.svelte",
      "src/routes/(app)/+page.svelte",
    ]) {
      assert.doesNotMatch(
        read(file),
        /<h[123][^>]*text-xs font-semibold uppercase/,
        `${file} still heads a section in 12px muted`,
      );
    }
  });

  test("the three screens the team lives in open with the same band", () => {
    for (const file of [
      "src/routes/(app)/+page.svelte",
      "src/routes/(app)/tasks/+page.svelte",
      "src/routes/(app)/companies/[id]/+page.svelte",
    ]) {
      const source = read(file);
      assert.match(source, /<PageHeader/, `${file} draws no title band`);
      // Its own <h1> would be a second answer to the question the band already answers.
      assert.doesNotMatch(source, /<h1\b/, `${file} still writes its own <h1>`);
    }
  });

  test("the client hub's two lanes are drawn as two kinds", () => {
    const hub = read("src/routes/(app)/companies/[id]/+page.svelte");
    assert.match(hub, /WORKING_LANE: LaneKind = \{ kind: "panel"/);
    assert.match(hub, /REGISTER_LANE: LaneKind = \{ kind: "register"/);
    // The band over the registers must be the ladder's, not the old muted string.
    assert.match(hub, /BAND_HEADING/);
  });
});
