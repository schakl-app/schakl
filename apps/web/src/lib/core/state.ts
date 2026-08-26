/**
 * The state palette (#404) — the five meanings a screen is allowed to shout in, and the one
 * rule that governs every one of them:
 *
 * > **A semantic state may never be expressed in the tenant's brand colour, and never in
 * > colour alone.**
 *
 * Both halves were being broken, and for the same reason: there was no palette, so each
 * surface invented one. `SummaryStrip` had a four-tone map, `burn.ts` had a three-step ladder
 * whose "fine" step was `bg-brand`, and eleven other screens hand-wrote a red. Colour therefore
 * carried exactly one meaning across the product — *bad* — which is why the team read the
 * interface as flat where it was quiet and alarming where it was not.
 *
 * **Why brand is barred.** `org_settings.primary_color` is tenant data (CLAUDE.md §7) and can
 * be anything. On one tenant we run today it is **gold** — visually indistinguishable from an
 * amber warning. So a state drawn in `--brand` says a different thing per tenant, which is not
 * a state at all. Brand is for identity and navigation; states come from here and do not move.
 *
 * **Why colour alone is barred.** `late`, `today` and `soon` are three adjacent hues on
 * purpose — urgency *is* a ramp, and a ramp is what a reader scans. Three adjacent hues are
 * also exactly what a red-green or a monochrome reader cannot separate, so every state carries
 * a glyph as well, and the glyphs are chosen to differ in *shape* rather than in decoration.
 * That is the same conclusion docs/UX.md already reached for the billable marker ("the glyph
 * carries the state as well as the colour, because a tenant's brand may be green") — stated
 * once here instead of per surface.
 *
 * `neutral` is deliberately hueless: it is the theme's own text, so it can never collide with
 * a brand colour, and "nothing is wrong here" reads as an absence rather than as a fifth hue
 * competing for attention.
 */
/**
 * `late`    — the moment has passed: overdue, unpaid past its term, over budget, monitor down.
 * `today`   — due now. Not a fault, but nobody may scroll past it.
 * `soon`    — approaching, and worth knowing about before it is either of the above.
 * `ok`      — actively fine. Not the absence of a problem; the presence of a good answer.
 * `neutral` — no state. The default, and the only one that is not a claim.
 */
export const UI_STATES = ["late", "today", "soon", "ok", "neutral"] as const;

export type UiState = (typeof UI_STATES)[number];

/**
 * The vocabulary the API already speaks (`SummaryTile.tone`, #364) mapped onto this one. The
 * API says how a figure *reads*; the palette says how it is drawn, and translating at the seam
 * is what stops a second answer growing beside this one. An unknown tone is `neutral` — a tone
 * a client does not recognise must never be louder than one it does.
 */
const FROM_TONE: Record<string, UiState> = {
  neutral: "neutral",
  good: "ok",
  warn: "soon",
  bad: "late",
};

export function stateFromTone(tone: string | null | undefined): UiState {
  return FROM_TONE[tone ?? "neutral"] ?? "neutral";
}

/**
 * Text. The figure itself, or a line of prose about one. Deliberately a *dark* shade in light
 * mode and a light one in dark mode: a state is read, not glanced at, and the 500-weights that
 * look right on a chip fail contrast on a sentence.
 */
const TEXT: Record<UiState, string> = {
  late: "text-red-700 dark:text-red-400",
  today: "text-orange-700 dark:text-orange-400",
  soon: "text-amber-700 dark:text-amber-400",
  ok: "text-emerald-700 dark:text-emerald-400",
  neutral: "text-text",
};

/** A pill: the state as a small standalone object, always beside its own word. */
const CHIP: Record<UiState, string> = {
  late: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
  today: "bg-orange-100 text-orange-800 dark:bg-orange-950 dark:text-orange-300",
  soon: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-300",
  ok: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  neutral: "bg-surface text-text-muted",
};

/** A filled mark: a bar's fill, a dot, a rail down the side of a row. */
const FILL: Record<UiState, string> = {
  late: "bg-red-500 dark:bg-red-400",
  today: "bg-orange-500 dark:bg-orange-400",
  soon: "bg-amber-500 dark:bg-amber-400",
  ok: "bg-emerald-500 dark:bg-emerald-400",
  neutral: "bg-text-muted",
};

/** An edge: a card or row border that carries the state without filling the whole surface. */
const BORDER: Record<UiState, string> = {
  late: "border-red-300 dark:border-red-800",
  today: "border-orange-300 dark:border-orange-800",
  soon: "border-amber-300 dark:border-amber-800",
  ok: "border-emerald-300 dark:border-emerald-800",
  neutral: "border-border",
};

/**
 * A chart mark drawn in a state (a donut slice, a plotted point). SVG paints through `fill`,
 * which `bg-*` cannot reach, so the same hues are stated once more as `fill-*` — same shades
 * as `FILL`, and only ever restated here.
 */
const SVG_FILL: Record<UiState, string> = {
  late: "fill-red-500 dark:fill-red-400",
  today: "fill-orange-500 dark:fill-orange-400",
  soon: "fill-amber-500 dark:fill-amber-400",
  ok: "fill-emerald-500 dark:fill-emerald-400",
  neutral: "fill-text-muted",
};

export function stateTextClass(state: UiState): string {
  return TEXT[state] ?? TEXT.neutral;
}

export function stateSvgFillClass(state: UiState): string {
  return SVG_FILL[state] ?? SVG_FILL.neutral;
}

export function stateChipClass(state: UiState): string {
  return CHIP[state] ?? CHIP.neutral;
}

export function stateFillClass(state: UiState): string {
  return FILL[state] ?? FILL.neutral;
}

export function stateBorderClass(state: UiState): string {
  return BORDER[state] ?? BORDER.neutral;
}

/**
 * The glyph lives in `core/ui/state-icons.ts`, not here, and the split is not cosmetic: this
 * module is the palette's *rules*, and rules that nothing else can catch have to be assertable
 * (`tests/unit/visual-system.test.ts` — "a state drawn in the tenant's brand looks correct on
 * the tenant the developer is testing against"). A lucide import is a `.svelte` file, which
 * node's test runner cannot load, so importing one here would make the one file that most needs
 * a test the one file that cannot have one. Everything above is plain strings, deliberately.
 */
