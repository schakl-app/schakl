import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

// Company groups (issue #191): the admin surface for the company data horizon. Groups scope
// which companies a member can *see*; roles keep scoping what they can *do* (§15).
export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "companies.group.manage")) throw redirect(303, "/");
  const api = apiFor(event);
  const [{ data: groups }, { data: companies }, { data: members }] = await Promise.all([
    api.GET("/api/v1/companies/groups"),
    // A name-only lookup: no COUNT, no aggregates (docs/PERFORMANCE.md).
    api.GET("/api/v1/companies", { params: { query: { limit: 200, count: false, sort: "name" } } }),
    api.GET("/api/v1/members"),
  ]);
  return {
    groups: groups ?? [],
    companies: (companies?.items ?? []).map((c) => ({ id: c.id, name: c.name })),
    members: members ?? [],
  };
};

export const actions: Actions = {
  create: async (event) => {
    const form = await event.request.formData();
    const name = String(form.get("name") ?? "").trim();
    if (!name) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).POST("/api/v1/companies/groups", {
      body: { name },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { saved: true };
  },

  // One save per editing surface (docs/UX.md): name + companies + members land together.
  save: async (event) => {
    const form = await event.request.formData();
    const group_id = String(form.get("id") ?? "");
    const name = String(form.get("name") ?? "").trim();
    if (!group_id || !name) return fail(400, { error: "errors.required" });
    const company_ids = form.getAll("company_ids").map(String);
    const membership_ids = form.getAll("membership_ids").map(String);
    const api = apiFor(event);
    const { error } = await api.PATCH("/api/v1/companies/groups/{group_id}", {
      params: { path: { group_id } },
      body: { name },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    const [companiesRes, membersRes] = await Promise.all([
      api.PUT("/api/v1/companies/groups/{group_id}/companies", {
        params: { path: { group_id } },
        body: { company_ids },
      }),
      api.PUT("/api/v1/companies/groups/{group_id}/memberships", {
        params: { path: { group_id } },
        body: { membership_ids },
      }),
    ]);
    if (companiesRes.error) return fail(400, { error: apiErrorKey(companiesRes.error).key });
    if (membersRes.error) return fail(400, { error: apiErrorKey(membersRes.error).key });
    return { saved: true };
  },

  delete: async (event) => {
    const form = await event.request.formData();
    const group_id = String(form.get("id") ?? "");
    if (group_id) {
      await apiFor(event).DELETE("/api/v1/companies/groups/{group_id}", {
        params: { path: { group_id } },
      });
    }
    return { deleted: true };
  },
};
