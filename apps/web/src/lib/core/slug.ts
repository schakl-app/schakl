/**
 * Definition-key slugs (issue #234).
 *
 * Tenant-admin definition screens (task statuses, custom fields, leave types, roles,
 * contact types, interaction kinds, time-entry types, subscription types) store an
 * immutable `key` next to the label. The tenant only ever types the label; the form
 * action derives the key with `slugify` and, because the key is invisible, translates a
 * duplicate-key 409 into an error against the label field via `createErrorKey`.
 */
import { apiErrorKey } from "./errors";

/** Lowercase `[a-z0-9_]` slug; 50 chars fits every definition API's max length. */
export function slugify(value: string): string {
  return value
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 50)
    .replace(/_+$/, "");
}

/**
 * The message key for a failed definition create. A duplicate-key conflict points at the
 * label the tenant actually typed ("that name is in use"); everything else passes through.
 */
export function createErrorKey(error: unknown, response: Response): string {
  const e = apiErrorKey(error);
  return response.status === 409 && e.key === "errors.conflict" ? "errors.label_in_use" : e.key;
}
