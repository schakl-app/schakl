/**
 * Brand-prefixed browser tab title (issue #71).
 *
 * Each page supplies only its own segment ("Klanten", a company name, …); the tenant's brand is
 * applied here once, so every tab reads "Acme · Klanten" instead of a bare, contextless page
 * name. The prefix is the tenant's runtime brand (Golden Rule 4), never a hardcoded product
 * name, and the separator/format go through i18n (Golden Rule 2) so a locale can reorder them.
 *
 * Honours `showBrandName`: a tenant that hides its name in the chrome gets a plain segment
 * title, not a prefixed one. Falls back to the bare segment when no brand is resolved — the
 * first-run wizard, or an unknown host.
 *
 * Reads `page.data.theme`, so it must be called during component render (inside `<svelte:head>`),
 * which is also what makes it render server-side with the rest of the head — matching the
 * brand-stamping the login page did by hand, now generalised to every tab.
 */
import { page } from "$app/state";
import { t } from "./i18n";

/**
 * Render a tenant tab-title template (#97): "{page}" is the page segment, "{brand}" the
 * runtime brand. A {brand} that resolves to nothing (unresolved host, or the tenant hides its
 * name) must not leave a dangling separator — "Klanten · " reads broken — so empty-token
 * separators are trimmed off the ends.
 */
export function renderTabTitle(template: string, segment: string, brand: string): string {
  const rendered = template.replaceAll("{page}", segment).replaceAll("{brand}", brand);
  return brand ? rendered.trim() : rendered.replace(/^[\s·—|:•-]+|[\s·—|:•-]+$/g, "").trim();
}

export function pageTitle(segment: string): string {
  const theme = page.data.theme;
  const brand = theme?.showBrandName === false ? "" : (theme?.brandName ?? "").trim();
  // The tenant's own template wins (#97); the i18n format stays the default.
  const template = (theme?.tabTitleTemplate ?? "").trim();
  if (template) return renderTabTitle(template, segment, brand);
  return brand ? t("common.page_title", { brand, segment }) : segment;
}
