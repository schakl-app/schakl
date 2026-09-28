import { bulkDeleteAction } from "$lib/core/bulk/actions.server";
import { readFilters } from "$lib/core/filters/types";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { readTablePref, resolveColumns } from "$lib/core/table/columns";
import { resolvePaging } from "$lib/core/table/paging";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import { MEETING_COLUMNS, MEETING_FILTERS, MEETINGS_TABLE_ID } from "$lib/modules/meetings/columns";

import type { Actions, PageServerLoad } from "./$types";

/**
 * The meetings register: every recorded meeting, newest first, paged (CLAUDE.md §9 — a list
 * screen pages, it never shows a prefix of itself). The URL is the view: `?company=`,
 * `?status=`, `?q=`, `?sort=`, `?page=`.
 */
export const load: PageServerLoad = async (event) => {
  event.depends("meetings:list");
  const api = apiFor(event);
  const { prefs } = await event.parent();
  const pref = readTablePref(prefs, MEETINGS_TABLE_ID);
  const resolved = resolveColumns(MEETING_COLUMNS, pref);
  const sort = event.url.searchParams.get("sort") ?? resolved.sort ?? undefined;
  const paging = resolvePaging(event.url, pref);
  // The bar and this load read the same keys from the same place, so what the controls show
  // and what the API was asked for cannot disagree (core/filters/types.ts).
  const filters = readFilters(event.url, [...MEETING_FILTERS]);

  const meetings = await api.GET("/api/v1/meetings", {
    params: {
      query: {
        limit: paging.limit,
        offset: paging.offset,
        company_id: filters.company,
        status: filters.status,
        q: filters.q,
        sort,
      },
    },
  });
  return {
    meetings: meetings.data?.items ?? [],
    total: meetings.data?.total ?? 0,
    paging,
    table: { pref, sort: sort ?? null, widths: resolved.widths },
    filters: {
      company_id: filters.company ?? "",
      status: filters.status ?? "",
      q: filters.q ?? "",
    },
    canWrite: can(event.locals.user, "meetings.meeting.write"),
    locale: event.locals.locale,
  };
};

export const actions: Actions = {
  /** Persist this user's column layout. Personal, in-view — never org settings (docs/UX.md). */
  saveTable: async (event) => {
    const form = await event.request.formData();
    await saveTablePref(event, MEETINGS_TABLE_ID, parseTablePref(form));
    return { tableSaved: true };
  },

  /**
   * Bulk delete, the one generic action a meeting takes — a title, a client and a day are each
   * that meeting's own, so there is nothing a selection could share. The API refuses a row a
   * worker is still reading and reports it beside the ones that went.
   */
  bulkDelete: (event) => bulkDeleteAction(event, "meeting"),
};
