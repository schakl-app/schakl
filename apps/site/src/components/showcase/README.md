# Showcase — animated product demos (internal)

> How the moving demos on the marketing site are built. Read this before adding or editing a
> demo. Not shipped to visitors — this is a contributor/agent note that lives next to the code.

The landing tour and the feature pages show the app in motion (create a client, add time,
budget burn, tasks, leave, white-label). They are **not video and not screenshots** — each is
the real app UI rebuilt in HTML + CSS and animated with CSS keyframes only. Video is heavy,
one-theme, one-language, and goes stale; a rebuilt frame is a few KB, stays crisp, adapts to
light/dark and locale, and only moves when allowed.

## Files

- `AppFrame.astro` — shared chrome (browser window + sidebar + top bar). A demo drops its
  screen into the slot. `aria-hidden` is set here (the demos are decorative).
- `Icon.astro` — inline Lucide icons (no icon font). Add new ones to the `paths` map.
- `labels.ts` — every in-demo string, one table per locale (verbatim from `messages/*.json`),
  plus `num()` / `eur()` for locale-correct formatting (nl-NL / en-GB via `Intl`) and `sample`
  data.
- `showcase.css` — tokens + the shared UI kit (button, input, card, chip, table, burn bar,
  checklist, avatar) + the fluid container-query sizing. Mirrors `apps/web/src/app.css`.
- `Demo*.astro` — one per demo. Static markup + a scoped `<style is:global>` with the demo's
  keyframes.
- `Showcase.astro` — the landing tour (rows + IntersectionObserver player).

Consumed by: `../LandingPage.astro` (the `showcase` block in `landing.json`) and
`../FeaturePage.astro` (a feature card's `demo` field in `src/data/features/*.json`).

## Fidelity rules (don't drift from these)

- Tokens match `apps/web/src/app.css`: brand `#4f46e5`, surface `#fafafa`/raised `#fff`, text
  `#171717`/muted `#737373`/border `#e5e5e5` (+ the dark values). Dark mode via
  `prefers-color-scheme` and a defensive `[data-theme="dark"]`.
- Burn scale = `apps/web/src/lib/core/burn.ts`: green `<75%`, amber `#f59e0b` `<100%`, red
  `#ef4444` `>=100%`; the bar clamps at 100% but the number doesn't; only the over state
  recolours text.
- Labels are real app strings, never invented. European formatting: `dd-mm-yyyy`, comma
  decimals, `€ ` with a space, hours unit `u` (nl) / `h` (en).

## The motion contract (the one thing to get right)

1. **Base styles = the completed/"after" frame.** With no JS and under
   `prefers-reduced-motion: reduce`, the demo rests on a meaningful result (new row present,
   totals ticked, checklist done). This is why every crossfade pair sets its *final* value as
   the base (`opacity: 1` on the "after" span, `0` on the "before"). Watch specificity:
   `.parent > span { opacity: 0 }` outranks `.child__a { opacity: 1 }` (the descendant element
   adds specificity) — target the visible child as `.parent > .child__a` so the base wins.
2. **Keyframes run empty → finished → empty**, so infinite loops are seamless, and are
   declared only under `@media (prefers-reduced-motion: no-preference) { .demo-x.is-playing … }`.
3. **A `.stagefade` veil is opaque at the loop boundary** (`0%,92%{opacity:0} 96%,100%{opacity:1}`)
   so the content snap from "after" back to "before" happens under cover — no seam flicker.
   (White-label needs no veil: its keyframes already return to the 0% state at 100%.)
4. **Play is gated by an IntersectionObserver** that adds `.is-playing` only while on screen and
   never when `prefers-reduced-motion` is set. Animate only `transform` / `opacity` / `width` /
   `background`.
5. **Elements whose only children are `position:absolute` need an explicit height** or they
   collapse/clip (e.g. the crossfade "reels").

## Adding a demo

1. Build `DemoThing.astro`: `<AppFrame locale active="…" class="demo-thing">`, compose the
   screen from `showcase.css` kit classes, and put keyframes in `<style is:global>` under
   `.demo-thing.is-playing`. Add a `<div class="stagefade">`.
2. Register it in the demo maps in `Showcase.astro` and `FeaturePage.astro`.
3. Wire it: `"demo": "thing"` in the feature file, and/or a `showcase` item in `landing.json`.

## Verifying frames

The MCP Playwright can't launch here — script `playwright-core` from the pnpm store against
`astro dev` (see the repo memory note). To inspect an exact keyframe, remove `.is-playing`,
inject `*{animation-delay:-<loop*fraction>s !important; animation-play-state:paused !important}`,
force a reflow, then add `.is-playing` — applying the negative delay *before* play makes the
frozen position exactly the fraction (the `*` selector doesn't reach `::before`/`::after`, so
check pseudo-element animations in the live loop or the base frame).
