import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { readTablePref } from "$lib/core/table/columns";
import { resolvePaging } from "$lib/core/table/paging";

import type { PageServerLoad } from "./$types";

const MEETINGS_TABLE_ID = "meetings";

/**
 * The meetings register: every recorded meeting, newest first, paged (CLAUDE.md §9 — a list
 * screen pages, it never shows a prefix of itself). The URL is the view: `?company=`,
 * `?status=`, `?q=`, `?page=`.
 */
export const load: PageServerLoad = async (event) => {
  event.depends("meetings:list");
  const api = apiFor(event);
  const { prefs } = await event.parent();
  const pref = readTablePref(prefs, MEETINGS_TABLE_ID);
  const paging = resolvePaging(event.url, pref);
  const params = event.url.searchParams;
  const company_id = params.get("company") || undefined;
  const status = params.get("status") || undefined;
  const q = params.get("q")?.trim() || undefined;

  const meetings = await api.GET("/api/v1/meetings", {
    params: { query: { limit: paging.limit, offset: paging.offset, company_id, status, q } },
  });
  return {
    meetings: meetings.data?.items ?? [],
    total: meetings.data?.total ?? 0,
    paging,
    filters: { company_id: company_id ?? "", status: status ?? "", q: q ?? "" },
    canWrite: can(event.locals.user, "meetings.meeting.write"),
  };
};
