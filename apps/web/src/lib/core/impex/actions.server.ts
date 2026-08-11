/**
 * Shared form actions for the import wizard (issue #77).
 *
 * Three submits against one form, so the wizard needs no server-side session: the browser's
 * own file input (or textarea) still holds the source, and each step re-posts it.
 *
 *   `inspect` — read the file, report its columns + samples and the suggested mapping
 *   `preview` — the API's dry run, with the mapping the user confirmed
 *   `commit`  — the same call, applied all-or-nothing
 *
 * The bytes travel with every step and nothing is staged server-side. The fingerprint from
 * `inspect` rides along, so swapping the file between mapping and importing is a 409 rather
 * than the wrong columns written into the right fields (a mapping is positional).
 *
 * The API is the authority on validation; these actions only relay its reports.
 */
import { fail } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor, type ApiEvent } from "$lib/core/session";

import type { components, paths } from "$lib/core/api/schema";

export type ImportReport = components["schemas"]["ImportReport"];
export type ImportRowError = components["schemas"]["ImportRowError"];
export type InspectReport = components["schemas"]["ImpexInspectReport"];
export type ImpexColumns = components["schemas"]["ImpexColumnsResponse"];
export type ImpexColumn = components["schemas"]["ImpexColumnInfo"];

/** Entity slugs with an import route — read off the generated client, never re-typed. */
type EntityOf<T> = T extends `/api/v1/impex/${infer E}/import` ? E : never;
export type ImportEntity = EntityOf<keyof paths>;
type ExportEntityOf<T> = T extends `/api/v1/impex/${infer E}/export` ? E : never;
/** Every entity with a CSV surface. Today import and export always come together. */
export type ImpexEntity = ExportEntityOf<keyof paths>;

type ImportPath = `/api/v1/impex/${ImportEntity}/import`;
type InspectPath = `/api/v1/impex/${ImportEntity}/inspect`;
type ColumnsPath = `/api/v1/impex/${ImportEntity}/columns`;

/**
 * Every entity slug the app knows how to import or export.
 *
 * The list is spelled out rather than derived so it can be *iterated* (the settings hub, the
 * export proxy's guard) — and the two conditional types below make the spelling out safe in
 * both directions: `satisfies` catches a slug the API does not have, `NoneMissing` catches an
 * entity the API gained that nothing here offers. A new descriptor is therefore a compile
 * error naming the slug, never a screen that silently lacks a button.
 */
export const IMPEX_ENTITIES = [
  "company",
  "contact",
  "project",
  "task",
  "time_entry",
  "subscription",
  "subscription_type",
  "subscription_template",
  "domain",
  "domain_tld_price",
  "website",
  "hosting",
  "uptime_monitor",
] as const satisfies readonly ImpexEntity[];

type NoneMissing =
  Exclude<ImpexEntity, (typeof IMPEX_ENTITIES)[number]> extends never
    ? true
    : "impex: an entity with a CSV route is missing from IMPEX_ENTITIES";
const _noneMissing: NoneMissing = true;
void _noneMissing;

/**
 * The filter vocabulary an export accepts, mirroring the API's `FILTER_PARAMS`.
 *
 * Which of these a given entity actually honours is the descriptor's business (its
 * `filters` tuple), and the API's generated signature rejects the rest — so the proxy
 * forwards from one list rather than keeping a second per-entity copy in sync.
 */
export const EXPORT_FILTERS = [
  "q",
  "status",
  "mine",
  "company_id",
  "project_id",
  "user_id",
  "date_from",
  "date_to",
  "hosting_id",
  "registrar_provider_id",
  "dns_provider_id",
  "invoiceable",
  "uptime_enabled",
  "sort",
] as const;

export function isExportable(entity: string): entity is ImpexEntity {
  return (IMPEX_ENTITIES as readonly string[]).includes(entity);
}

function isImportable(entity: string): entity is ImportEntity {
  return (IMPEX_ENTITIES as readonly string[]).includes(entity as ImportEntity);
}

/**
 * The picked source, as multipart the API accepts either way.
 *
 * A file and a paste are one concept to the user and two very different things to the
 * platform (a paste is capped far lower — Starlette truncates a large non-file part), so the
 * decision is made once, here, rather than at each of the three call sites.
 */
function sourceBody(form: FormData): FormData | null {
  const body = new FormData();
  const file = form.get("file");
  const text = String(form.get("text") ?? "");
  if (file instanceof File && file.size > 0) {
    body.append("file", file, file.name || "import.csv");
  } else if (text.trim()) {
    body.append("text", text);
  } else {
    return null;
  }
  const sheet = String(form.get("sheet") ?? "");
  if (sheet) body.append("sheet", sheet);
  // The checkbox is posted as a hidden "false" followed by the box's own "true", because an
  // unchecked box posts nothing at all and "nothing" would read as the default. Last wins.
  const header = form.getAll("has_header");
  body.append(
    "has_header",
    String(header[header.length - 1] ?? "true") === "true" ? "true" : "false",
  );
  return body;
}

/**
 * `map_<index>` fields → the API's `{index: key}` object.
 *
 * Assembled server-side rather than posted as JSON the browser built: one hidden input per
 * file column is what a `Combobox` already posts, and it keeps the wizard working without a
 * second client-side representation of the same state to keep in sync.
 */
function mappingFrom(form: FormData): string {
  const mapping: Record<string, string> = {};
  for (const [name, value] of form.entries()) {
    const match = /^map_(\d+)$/.exec(name);
    if (match && typeof value === "string" && value) mapping[match[1]] = value;
  }
  return JSON.stringify(mapping);
}

/** Hub variant: validate the slug from the query string, then delegate. */
export async function impexActionFor(event: ApiEvent, entity: string) {
  if (!isImportable(entity)) return fail(400, { impexError: "errors.not_found" });
  return impexAction(event, entity);
}

export async function impexAction(event: ApiEvent, entity: ImportEntity) {
  const form = await event.request.formData();
  const body = sourceBody(form);
  if (!body) return fail(400, { impexError: "impex.errors.no_source" });

  const mode = String(form.get("mode") ?? "inspect");
  const api = apiFor(event);
  // The generated schema types a multipart body as a flat record of strings; hand the real
  // FormData straight to fetch so it sets the multipart boundary itself.
  const multipart = {
    body: body as unknown as { file: string; has_header: boolean },
    bodySerializer: (b: unknown) => b as FormData,
  };

  if (mode === "inspect") {
    const [inspected, columns] = await Promise.all([
      api.POST(`/api/v1/impex/${entity}/inspect` as InspectPath, multipart),
      api.GET(`/api/v1/impex/${entity}/columns` as ColumnsPath),
    ]);
    if (inspected.error || !inspected.data) {
      return fail(400, { impexError: apiErrorKey(inspected.error).key });
    }
    return {
      impexInspect: inspected.data as InspectReport,
      impexColumns: (columns.data ?? null) as ImpexColumns | null,
    };
  }

  body.append("mapping", mappingFrom(form));
  const matchKey = String(form.get("match_key") ?? "");
  if (matchKey) body.append("match_key", matchKey);
  const fingerprint = String(form.get("fingerprint") ?? "");
  if (fingerprint) body.append("fingerprint", fingerprint);

  const { data, error } = await api.POST(`/api/v1/impex/${entity}/import` as ImportPath, {
    params: { query: { dry_run: mode !== "commit" } },
    ...multipart,
  });
  if (error || !data) return fail(400, { impexError: apiErrorKey(error).key });
  return { impex: data as ImportReport };
}
