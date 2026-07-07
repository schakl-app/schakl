/**
 * Registers the `custom-vlotrDefault` Paraglide strategy (CLAUDE.md §8).
 *
 * The compiled strategy order is `cookie → custom-vlotrDefault → baseLocale`, so:
 *   - a user's explicit choice (the PARAGLIDE_LOCALE cookie) wins;
 *   - otherwise we default to **Dutch** — the app ships showing `nl` even though `en` is the
 *     source locale;
 *   - `baseLocale` (`en`) is only the last-resort fallback.
 *
 * Imported for its side effect from both server and client hooks.
 */
import {
  defineCustomClientStrategy,
  defineCustomServerStrategy,
  isServer,
} from "$lib/paraglide/runtime";

// P0 default display locale. P1 can make this per-tenant from org_settings.default_locale.
const DEFAULT_LOCALE = "nl";

if (isServer) {
  defineCustomServerStrategy("custom-vlotrDefault", {
    getLocale: () => DEFAULT_LOCALE,
  });
} else {
  defineCustomClientStrategy("custom-vlotrDefault", {
    getLocale: () => DEFAULT_LOCALE,
    setLocale: () => {},
  });
}
