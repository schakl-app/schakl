import { error } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  const company_id = event.params.id;

  const { data: company } = await api.GET("/api/v1/companies/{company_id}", {
    params: { path: { company_id } },
  });
  if (!company) throw error(404, { code: "not_found", message: "errors.not_found" });

  const { data: panels } = await api.GET("/api/v1/companies/{company_id}/panels", {
    params: { path: { company_id } },
  });

  return { company, panels: panels ?? [] };
};
