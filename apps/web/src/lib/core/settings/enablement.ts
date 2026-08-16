/**
 * What Instellingen → Modules and Instellingen → Integraties both reason about (issue #378).
 *
 * The two screens edit two halves of **one** list: `PATCH /meta/tenant` takes the whole
 * `enabled_modules` array, and `ensure_requirements_met` judges the whole resulting set, not the
 * delta (CLAUDE.md §6a). So neither screen may reason about its own kind alone — an integration on
 * the Integraties screen requires a module on the Modules one, and posting only your own half
 * would wipe the other. Everything that spans both lives here rather than being written twice.
 *
 * **Client-safe on purpose.** The load and the action live in `enablement.server.ts`, because both
 * screens' *components* import `effectiveModules` and this type — and a shared module that also
 * imported `$lib/core/session` dragged `$env/dynamic/private` into the browser bundle and failed
 * the production build outright. The dev server never notices; only `vite build` does.
 */
export interface EnablementData {
  /** Every module this installation ships, whatever this workspace has switched on. */
  available: string[];
  /** What this workspace has switched on right now. */
  enabled: string[];
  licensed: string[];
  entitled: string[];
  kinds: Record<string, string>;
  requires: Record<string, string[]>;
  deployment: string;
}

/**
 * The valid set nearest to what is ticked: everything whose requirements are themselves in it.
 *
 * A fixpoint rather than one pass, because a requirement may itself be an integration —
 * `google_ads` needs `google` — so dropping Google in one round has to drop Google Ads in the
 * next. Twenty-six names and two rounds in practice, so assuming a depth would buy nothing and
 * would be wrong the first time an integration requires an integration that requires one.
 */
export function effectiveModules(
  ticked: readonly string[],
  requires: Record<string, string[]>,
): string[] {
  let keep = [...ticked];
  for (;;) {
    const next = keep.filter((name) => (requires[name] ?? []).every((need) => keep.includes(need)));
    if (next.length === keep.length) return next;
    keep = next;
  }
}
