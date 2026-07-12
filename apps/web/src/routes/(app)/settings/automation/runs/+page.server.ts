import { redirect } from "@sveltejs/kit";

import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "automation.run.read")) throw redirect(303, "/settings");
  const api = apiFor(event);
  const ruleId = event.url.searchParams.get("rule_id");
  const [runs, rules] = await Promise.all([
    api.GET("/api/v1/automation/runs", {
      params: { query: ruleId ? { rule_id: ruleId } : {} },
    }),
    // The filter select; someone with run.read but not rule.read just loses the filter.
    can(event.locals.user, "automation.rule.read")
      ? api.GET("/api/v1/automation/rules")
      : Promise.resolve({ data: null }),
  ]);
  return {
    page: runs.data ?? { items: [], total: 0, limit: 50, offset: 0 },
    rules: (rules.data ?? []).map((rule) => ({ id: rule.id, name: rule.name })),
    ruleId,
  };
};
