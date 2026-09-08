/**
 * The entities the trash can hold (docs/TRASH.md), as the web knows them.
 *
 * The API derives its trash routes from each module's `TrashableSpec`; this is the web's half
 * of the same list — the permission that opens the trash for that type, the label of the type,
 * and where a restored record lives. Importing nothing, like `notifications/href.ts`, so a unit
 * test can pin it without a Vite resolver.
 */
export interface TrashEntity {
  /** The API's `entity_type` — the path segment under `/api/v1/trash/`. */
  entity: string;
  /** The entity's own delete permission: trashing, restoring and purging all ride it. */
  permission: string;
  /** `t()` key naming the type on a mixed trash screen. */
  labelKey: string;
  /** The record's own page — where a restore leads back to. */
  href: (id: string) => string;
}

export const TRASH_ENTITIES: readonly TrashEntity[] = [
  {
    entity: "company",
    permission: "companies.company.delete",
    labelKey: "trash.entity.company",
    href: (id) => `/companies/${id}`,
  },
];

export function trashEntity(entity: string): TrashEntity | undefined {
  return TRASH_ENTITIES.find((row) => row.entity === entity);
}
