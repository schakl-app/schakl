import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { readFilters } from "$lib/core/filters/types";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { readTablePref, resolveColumns } from "$lib/core/table/columns";
import { resolvePaging } from "$lib/core/table/paging";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import {
  POST_COLUMNS,
  POST_FILTERS,
  POSTS_TABLE_ID,
  STATUS_ALL,
} from "$lib/integrations/meta/columns";
import { WORKING } from "$lib/integrations/meta/format";

import type { Actions, PageServerLoad } from "./$types";

/**
 * The planner: every post, paged (CLAUDE.md §9 — a list screen pages, it never shows a prefix
 * of itself). The URL is the view: `?company=`, `?channel=`, `?status=`, `?q=`, `?sort=`,
 * `?page=`.
 *
 * **It opens on what is still going on** (#329). Absent `?status=` means the working set —
 * drafts, what waits for approval, what is scheduled, what failed — and `?status=all` means
 * everything, the published and the cancelled included. The *screen* picks that default; the
 * endpoint's own default stays "everything", because the agenda feed and the generated MCP tool
 * read the same endpoint and narrowing it there would change what they are told exists.
 *
 * Two reads, in parallel: the page, and the counts the pills print.
 */
export const load: PageServerLoad = async (event) => {
  event.depends("meta:posts");
  const api = apiFor(event);
  const { prefs } = await event.parent();
  const pref = readTablePref(prefs, POSTS_TABLE_ID);
  const resolved = resolveColumns(POST_COLUMNS, pref);
  const sort = event.url.searchParams.get("sort") ?? resolved.sort ?? undefined;
  const paging = resolvePaging(event.url, pref);
  const filters = readFilters(event.url, [...POST_FILTERS]);

  const statusFilter = filters.status ?? "";
  const status = statusFilter === STATUS_ALL ? undefined : statusFilter || WORKING;
  // A list of what is *coming* reads soonest-first, which is the API's own default; the
  // archive reads newest-first. Only where nobody chose: a sort the user picked is the sort
  // they get.
  const archive = statusFilter === "published" || statusFilter === "cancelled";

  const [posts, counts] = await Promise.all([
    api.GET("/api/v1/meta-business/posts", {
      params: {
        query: {
          limit: paging.limit,
          offset: paging.offset,
          status,
          company_id: filters.company,
          channel: filters.channel,
          q: filters.q,
          sort: sort ?? (archive ? "-when" : undefined),
        },
      },
    }),
    api.GET("/api/v1/meta-business/posts/counts", {
      params: { query: { company_id: filters.company } },
    }),
  ]);
  return {
    posts: posts.data?.items ?? [],
    total: posts.data?.total ?? 0,
    // A read that failed is said, never drawn as an empty planner: "geen berichten" is a
    // different answer to a different question.
    loadError: posts.error ? apiErrorKey(posts.error, "errors.server").key : null,
    counts: counts.data ?? { by_status: {}, working: 0, total: 0 },
    paging,
    table: { pref, sort: sort ?? null, widths: resolved.widths },
    filters: {
      company_id: filters.company ?? "",
      channel: filters.channel ?? "",
      status: statusFilter,
      q: filters.q ?? "",
    },
    locale: event.locals.locale,
  };
};

export const actions: Actions = {
  /** Persist this user's column layout. Personal, in-view — never org settings (docs/UX.md). */
  saveTable: async (event) => {
    const form = await event.request.formData();
    await saveTablePref(event, POSTS_TABLE_ID, parseTablePref(form));
    return { tableSaved: true };
  },

  /**
   * A new draft, and then its own page. Create-then-edit (#230): what has to be decided before
   * a post exists is *where it goes*, because that is what decides whose it is and what the
   * channel will accept. The words are written on the page it lands on.
   */
  create: async (event) => {
    if (!can(event.locals.user, "meta.post.write")) {
      return fail(403, { createError: "errors.forbidden" });
    }
    const form = await event.request.formData();
    const asset_ids = form.getAll("asset_ids").map(String).filter(Boolean);
    if (asset_ids.length === 0) {
      return fail(400, { createError: "meta.issue.no_channel" });
    }
    const day = String(form.get("day") ?? "").trim();
    const time = String(form.get("time") ?? "").trim();
    const { data, error } = await apiFor(event).POST("/api/v1/meta-business/posts", {
      body: {
        asset_ids,
        body: "",
        notes: "",
        format: form.get("format") === "reel" ? "reel" : "post",
        // Naked: the API reads a naive clock on the org's own calendar (CLAUDE.md §8).
        scheduled_at: day ? `${day}T${time || "09:00"}:00` : null,
      },
    });
    if (error || !data) {
      const failure = apiErrorKey(error, "errors.server");
      return fail(400, { createError: failure.fields?.asset_ids ?? failure.key });
    }
    throw redirect(303, `/marketing/social/${data.id}`);
  },
};
