import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import type { TimeonRun, TimeonWorkspace } from "$lib/integrations/timeon/types";

import type { Actions, PageServerLoad } from "./$types";

/**
 * The Timeon sync workspace: what the last runs did, what is waiting for a decision, and the
 * button that runs another.
 *
 * It is a *screen* rather than a tab under Instellingen because it produces work. A conflict has
 * to be settled by a person, and "a surface that has to be found is one that is not kept up to
 * date" (the availability rule, CLAUDE.md §14). Configuration stays under Instellingen, where
 * docs/UX.md principle 6 puts it, and the two link to each other.
 *
 * **The shell never calls Timeon.** `GET /timeon/workspace` answers from stored rows in one round
 * trip — the four-reads-one-page rule from docs/GOOGLE_TAG_MANAGER.md §3a — so the page renders
 * during an outage, which is exactly when somebody opens it. Running a sync is the explicit act,
 * and it is a button.
 *
 * **The account and the filter are in the URL** (`?account=`, `?status=`), so the back button
 * lands where the user left and a view of "alleen conflicten" is a link somebody can paste.
 */
export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "timeon.sync.run")) throw redirect(303, "/");

  const typed = apiFor(event);
  const requested = event.url.searchParams.get("account");
  const workspace =
    (await typed.GET("/api/v1/timeon/workspace")).data ?? ({} as Partial<TimeonWorkspace>);
  const accounts = workspace.accounts ?? [];
  const selectedId = accounts.find((a) => a.id === requested)?.id ?? accounts[0]?.id ?? null;

  // A pairing list is the *second* question this screen answers, so it is filtered by default to
  // the rows that need somebody — `drift`, `missing`, `error`. "Alles" sits beside it, because a
  // list that silently leaves rows out looks identical to one that has none (#329's rule).
  const linkStatus = event.url.searchParams.get("links") ?? "attention";
  const ATTENTION = ["drift", "missing", "error"];

  const [conflicts, runs, links] = await Promise.all([
    selectedId
      ? typed.GET("/api/v1/timeon/conflicts", {
          params: { query: { account_id: selectedId, status: "open", limit: 50, offset: 0 } },
        })
      : Promise.resolve(null),
    selectedId
      ? typed.GET("/api/v1/timeon/runs", {
          params: { query: { account_id: selectedId, limit: 20, offset: 0 } },
        })
      : Promise.resolve(null),
    selectedId
      ? Promise.all(
          (linkStatus === "all" ? [undefined] : ATTENTION).map((status) =>
            typed.GET("/api/v1/timeon/links", {
              params: {
                query: { account_id: selectedId, status, limit: 50, offset: 0 },
              },
            }),
          ),
        )
      : Promise.resolve([]),
  ]);

  return {
    accounts,
    selectedId,
    linkStatus,
    conflicts: conflicts?.data ?? [],
    runs: runs?.data ?? [],
    links: links.flatMap((res) => res?.data ?? []),
    serverTime: workspace.server_time ?? new Date().toISOString(),
    mayWrite: can(event.locals.user, "timeon.sync.write"),
    // #314: writing into schakl is `time`'s gate, not this integration's, and the API asks for
    // both. Mirroring only one here would draw a button the API then refuses, and the 403 could
    // not say which half was missing (#310).
    mayWriteHours: can(event.locals.user, "time.entry.write", "any"),
  };
};

export const actions: Actions = {
  sync: async (event) => {
    const form = await event.request.formData();
    const account_id = String(form.get("account_id") ?? "");
    if (!account_id) return fail(400, { error: "errors.required" });
    const from = String(form.get("window_from") ?? "").trim();
    const to = String(form.get("window_to") ?? "").trim();
    const { data, error } = await apiFor(event).POST("/api/v1/timeon/accounts/{account_id}/sync", {
      params: { path: { account_id } },
      body: {
        kind: String(form.get("kind") ?? "hours") as never,
        // The default is a **dry run** at the API too. The flag is only ever false because a
        // button that says so was pressed — a sync that writes by default is one whose first
        // press is irreversible.
        dry_run: form.get("apply") !== "1",
        window_from: from || null,
        window_to: to || null,
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { run: (data ?? null) as TimeonRun | null };
  },

  resolve: async (event) => {
    const form = await event.request.formData();
    const conflict_id = String(form.get("conflict_id") ?? "");
    const resolution = String(form.get("resolution") ?? "");
    if (!conflict_id || !resolution) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).POST("/api/v1/timeon/conflicts/{conflict_id}/resolve", {
      params: { path: { conflict_id } },
      body: { resolution: resolution as never, note: null },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { resolved: true };
  },

  unpair: async (event) => {
    const form = await event.request.formData();
    const link_id = String(form.get("link_id") ?? "");
    if (!link_id) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).DELETE("/api/v1/timeon/links/{link_id}", {
      params: { path: { link_id } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { unpaired: true };
  },
};
