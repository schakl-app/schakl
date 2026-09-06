import { redirect } from "@sveltejs/kit";

import type { PageServerLoad } from "./$types";

/** Productiviteit became Medewerkers; a bookmarked link keeps working, filters and all. */
export const load: PageServerLoad = async (event) => {
  const query = event.url.search;
  throw redirect(301, `/overview/employees${query}`);
};
