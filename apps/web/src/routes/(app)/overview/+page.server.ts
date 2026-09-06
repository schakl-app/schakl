import { redirect } from "@sveltejs/kit";

import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { readYear } from "$lib/core/today";

import type { PageServerLoad } from "./$types";

/**
 * The query keys the hours report reads. `/overview` *was* the hours report until it became a
 * tab, and every link into it carried one of these — a bookmark, a dashboard tile, a task's
 * "hours" figure. They all still land where they meant to.
 */
const HOURS_REPORT_KEYS = [
  "user_id",
  "company_id",
  "project_id",
  "task_id",
  "date_from",
  "date_to",
  "status",
  "entry_type",
  "sort",
];

export const load: PageServerLoad = async (event) => {
  const q = event.url.searchParams;
  if (HOURS_REPORT_KEYS.some((key) => q.has(key))) {
    throw redirect(301, `/overview/hours?${q.toString()}`);
  }
  // The section layout lets a marketing-only manager in; the landing page is the time report's.
  const user = event.locals.user;
  if (!can(user, "time.report.read")) throw redirect(303, "/overview/marketing");

  const api = apiFor(event);
  const year = readYear(event.url);
  const enabled = event.locals.theme?.enabledModules ?? [];
  // The ledger's turnover is the invoicing *module*'s figure — `:any`, never a document read
  // (#266) — and it is drawn only where the module is on. Without it the year's revenue is what
  // the billable hours were worth, which is the same page with one honest sentence more.
  const invoicing = enabled.includes("invoicing") && can(user, "invoicing.invoice.read", "any");
  const projects = enabled.includes("projects") && can(user, "projects.project.read");

  const yearFrom = `${year}-01-01`;
  const yearTo = `${year}-12-31`;
  // One promise, streamed behind the shell (docs/PERFORMANCE.md): the heading, the year stepper
  // and the tab row render at once, and the five aggregates fill in behind them. Each read
  // fails alone — a tile that cannot be answered is left out rather than taking the page down.
  const payload = Promise.all([
    invoicing
      ? api
          .GET("/api/v1/invoicing/stats/revenue", { params: { query: { year } } })
          .then((r) => r.data ?? null)
          .catch(() => null)
      : Promise.resolve(null),
    invoicing
      ? api
          .GET("/api/v1/invoicing/summary")
          .then((r) => r.data ?? null)
          .catch(() => null)
      : Promise.resolve(null),
    api
      .GET("/api/v1/time/stats/revenue", { params: { query: { year } } })
      .then((r) => r.data ?? null)
      .catch(() => null),
    api
      .GET("/api/v1/time/stats/productivity", {
        params: { query: { date_from: yearFrom, date_to: yearTo } },
      })
      .then((r) => r.data ?? null)
      .catch(() => null),
    projects
      ? api
          .GET("/api/v1/projects/dashboard-budgets", { params: { query: { limit: 5 } } })
          .then((r) => r.data ?? null)
          .catch(() => null)
      : Promise.resolve(null),
    // The hours-value ranking names clients by id only; the ledger's ranking carries names.
    // So the lookup is fetched exactly when it is the ranking that will be drawn.
    invoicing
      ? Promise.resolve([])
      : api
          .GET("/api/v1/companies", {
            params: { query: { limit: 200, offset: 0, count: false, sort: "name" } },
          })
          .then((r) => r.data?.items ?? [])
          .catch(() => []),
  ]).then(([invoiced, summary, hoursValue, team, budgets, companies]) => ({
    invoiced,
    summary,
    hoursValue,
    team,
    budgets,
    companies,
  }));

  return { year, yearFrom, yearTo, invoicing, projects, payload };
};
