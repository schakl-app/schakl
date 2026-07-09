/**
 * Per-tenant runtime theming (CLAUDE.md §7, Golden Rule 4).
 *
 * Branding comes from the API's org settings; nothing is hardcoded. The server load applies it
 * via CSS custom properties on <html> at first render, and the dynamic manifest/favicon routes
 * reuse the same values.
 */

export interface OrgTheme {
  brandName: string;
  showBrandName: boolean;
  logoUrl: string | null;
  faviconUrl: string | null;
  primaryColor: string;
  accentColor: string;
  defaultLocale: string;
  enabledModules: string[];
  /** False when the hostname resolved to no org (unknown host, or a fresh install). */
  resolved: boolean;
  /** The org exists but is suspended: branding renders, every signed-in request is blocked. */
  suspended: boolean;
}

// Neutral fallback used only before/without tenant settings — not a product brand.
export const DEFAULT_THEME: OrgTheme = {
  brandName: "",
  showBrandName: true,
  logoUrl: null,
  faviconUrl: null,
  primaryColor: "#4f46e5",
  accentColor: "#0ea5e9",
  defaultLocale: "nl",
  enabledModules: ["companies"],
  resolved: false,
  suspended: false,
};

// The API validates colours as hex on write; re-check here because the value is interpolated
// into an HTML attribute, and fall back rather than emit anything else.
const HEX_COLOR = /^#[0-9a-fA-F]{3,8}$/;

function safeColor(value: string, fallback: string): string {
  return HEX_COLOR.test(value) ? value : fallback;
}

// Must match app.css's `[data-theme="dark"] { --surface: ... }` — the surface a tenant's brand
// colour is checked against when deriving its dark-mode variant. Duplicated because app.css's
// tokens aren't reachable from TS; keep the two in sync by hand.
const DARK_SURFACE = "#171717";
const MIN_CONTRAST = 3; // WCAG AA for non-text UI components (buttons, focus rings, accents).

function hexToRgb(hex: string): [number, number, number] {
  const clean = hex.length === 4 ? hex.replace(/[0-9a-f]/gi, (c) => c + c) : hex; // #rgb -> #rrggbb
  const n = parseInt(clean.slice(1, 7), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function rgbToHex(r: number, g: number, b: number): string {
  const c = (v: number) =>
    Math.round(Math.min(255, Math.max(0, v)))
      .toString(16)
      .padStart(2, "0");
  return `#${c(r)}${c(g)}${c(b)}`;
}

// WCAG relative luminance.
function luminance([r, g, b]: [number, number, number]): number {
  const chan = (v: number) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b);
}

function contrastRatio(a: [number, number, number], b: [number, number, number]): number {
  const [l1, l2] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (l1 + 0.05) / (l2 + 0.05);
}

function rgbToHsl([r, g, b]: [number, number, number]): [number, number, number] {
  const rn = r / 255,
    gn = g / 255,
    bn = b / 255;
  const max = Math.max(rn, gn, bn),
    min = Math.min(rn, gn, bn);
  const l = (max + min) / 2;
  if (max === min) return [0, 0, l];
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h: number;
  if (max === rn) h = ((gn - bn) / d + (gn < bn ? 6 : 0)) * 60;
  else if (max === gn) h = ((bn - rn) / d + 2) * 60;
  else h = ((rn - gn) / d + 4) * 60;
  return [h, s, l];
}

function hslToRgb(h: number, s: number, l: number): [number, number, number] {
  if (s === 0) return [l * 255, l * 255, l * 255];
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  const hk = h / 360;
  const chan = (t: number) => {
    let tt = t;
    if (tt < 0) tt += 1;
    if (tt > 1) tt -= 1;
    if (tt < 1 / 6) return p + (q - p) * 6 * tt;
    if (tt < 1 / 2) return q;
    if (tt < 2 / 3) return p + (q - p) * (2 / 3 - tt) * 6;
    return p;
  };
  return [chan(hk + 1 / 3) * 255, chan(hk) * 255, chan(hk - 1 / 3) * 255];
}

/**
 * Lighten a tenant colour (in HSL) until it reads clearly against a dark surface, rather than
 * using the raw tenant hex as-is — a dark navy brand colour would otherwise be nearly invisible
 * on a dark background. Desaturates slightly too, since very saturated colours can still look
 * muddy at high lightness. Hue is preserved so the tenant's colour identity survives.
 */
function deriveOnDark(hex: string): string {
  const rgb = hexToRgb(hex);
  const dark = hexToRgb(DARK_SURFACE);
  if (contrastRatio(rgb, dark) >= MIN_CONTRAST) return hex;
  const [h, initialS, initialL] = rgbToHsl(rgb);
  const s = Math.max(initialS * 0.85, 0.35);
  let l = initialL;
  for (let i = 0; i < 20 && contrastRatio(hslToRgb(h, s, l), dark) < MIN_CONTRAST; i++) {
    l = Math.min(l + 0.04, 0.92);
  }
  return rgbToHex(...hslToRgb(h, s, l));
}

/**
 * Inline style string stamped onto <html> so SSR paints the brand colours immediately.
 *
 * It must land on the root element, not a wrapper: `accent-color` is an inherited property
 * computed once at :root, so a variable overridden further down the tree never reaches the
 * native form controls (date/time pickers, checkboxes, radios, range).
 *
 * `scheme` is the *resolved* colour scheme (never "system" — callers resolve that first, see
 * hooks.server.ts / theme-mode.svelte.ts); on "dark" the brand colours are contrast-corrected
 * against the dark surface instead of used raw.
 */
export function themeStyle(theme: OrgTheme, scheme: "light" | "dark" = "light"): string {
  let primary = safeColor(theme.primaryColor, DEFAULT_THEME.primaryColor);
  let accent = safeColor(theme.accentColor, DEFAULT_THEME.accentColor);
  if (scheme === "dark") {
    primary = deriveOnDark(primary);
    accent = deriveOnDark(accent);
  }
  return `--brand-primary:${primary};--brand-accent:${accent};`;
}
