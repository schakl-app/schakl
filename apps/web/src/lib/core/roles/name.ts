/**
 * A role's display name is **tenant data**, not a Paraglide key: an agency names its own roles.
 * The seeded system roles ship with `{nl, en}` filled in, so they read correctly out of the box.
 */
import type { RoleRead } from "./permissions";

export function localeName(role: Pick<RoleRead, "key" | "name_i18n">, locale: string): string {
  return role.name_i18n[locale] || role.name_i18n.en || role.name_i18n.nl || role.key;
}

export function localeDescription(
  role: Pick<RoleRead, "description_i18n">,
  locale: string,
): string {
  return role.description_i18n[locale] || role.description_i18n.en || "";
}
