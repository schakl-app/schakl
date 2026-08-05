import { redirect } from "@sveltejs/kit";

import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { resolvePaging } from "$lib/core/table/paging";

import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "automation.run.read")) throw redirect(303, "/settings");
  const api = apiFor(event);
  const ruleId = event.url.searchParams.get("rule_id");
  // A run log only grows, so its first fifty are the least interesting thing in it once the
  // instance has been running a while (`paging.ts`).
  const paging = resolvePaging(event.url);
  const [runs, rules] = await Promise.all([
    api.GET("/api/v1/automation/runs", {
      params: {
        query: {
          limit: paging.limit,
          offset: paging.offset,
          ...(ruleId ? { rule_id: ruleId } : {}),
        },
      },
    }),
    // The filter select; someone with run.read but not rule.read just loses the filter.
    can(event.locals.user, "automation.rule.read")
      ? api.GET("/api/v1/automation/rules")
      : Promise.resolve({ data: null }),
  ]);
  return {
    page: runs.data ?? { items: [], total: 0, limit: paging.limit, offset: paging.offset },
    paging,
    rules: (rules.data ?? []).map((rule) => ({ id: rule.id, name: rule.name })),
    ruleId,
  };
};
