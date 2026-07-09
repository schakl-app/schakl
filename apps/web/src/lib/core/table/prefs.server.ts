/**
 * Persist one list's column layout (#24).
 *
 * `PUT /api/v1/prefs` shallow-merges by top-level namespace, so writing `{tables: {companies: …}}`
 * keeps every *other* list's layout but replaces this one wholesale. The caller therefore sends
 * the complete layout — columns, sort and widths together — never a partial patch, or the fields
 * it omitted would be erased.
 *
 * `.server.ts`: this reaches for the request-scoped API client and must never be bundled to the
 * browser.
 */
import type { RequestEvent } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";
import type { TablePref } from "$lib/core/table/columns";

export async function saveTablePref(
  event: RequestEvent,
  listId: string,
  pref: TablePref,
): Promise<void> {
  await apiFor(event).PUT("/api/v1/prefs", {
    body: { prefs: { tables: { [listId]: pref } } },
  });
}

/** Decode the layout a `?/saveTable` form action posts. Junk degrades to "unchanged", not a 500. */
export function parseTablePref(form: FormData): TablePref {
  const columns = String(form.get("columns") ?? "")
    .split(",")
    .map((key) => key.trim())
    .filter(Boolean);
  const sort = String(form.get("sort") ?? "").trim();

  let widths: Record<string, number> = {};
  try {
    const parsed: unknown = JSON.parse(String(form.get("widths") ?? "{}"));
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      widths = Object.fromEntries(
        Object.entries(parsed as Record<string, unknown>).filter(
          ([, v]) => typeof v === "number" && Number.isFinite(v) && v > 0,
        ),
      ) as Record<string, number>;
    }
  } catch {
    widths = {};
  }

  return { columns, sort: sort || null, widths };
}
