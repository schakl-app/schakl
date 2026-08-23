/**
 * The glyph half of the state palette (#404) — the half that survives greyscale, and the half a
 * colour-blind reader actually reads.
 *
 * Shapes over decoration: an exclamation in a ring, a clock, a hollow ring, a tick. Nothing here
 * is `--brand`, and nothing here is a hue: the glyph must still separate the states when the
 * colour does not, which is the whole reason it exists.
 *
 * `neutral` has **none**, and that is the rule rather than an omission. A mark beside every
 * quiet figure is the "wash of amber cards" mistake wearing an icon: it spends attention on the
 * rows that have nothing to say, which is the one budget a state palette exists to protect. The
 * absence is the mark.
 *
 * Separate from `core/state.ts` because a lucide icon is a `.svelte` file and the palette's
 * rules are asserted by a plain-node test — see the note at the foot of that file.
 */
import { CircleAlert, CircleCheck, CircleDot, Clock } from "@lucide/svelte";
import type { Component } from "svelte";

import type { UiState } from "$lib/core/state";

const ICON: Record<UiState, Component | null> = {
  late: CircleAlert,
  today: Clock,
  soon: CircleDot,
  ok: CircleCheck,
  neutral: null,
};

export function stateIcon(state: UiState): Component | null {
  return ICON[state] ?? null;
}
