import { redirect } from "@sveltejs/kit";

import type { PageServerLoad } from "./$types";

// The catalog moved onto the subscriptions section itself as sub-route tabs (#229);
// old links and the settings card land on the standard subscriptions tab.
export const load: PageServerLoad = () => {
  throw redirect(301, "/subscriptions/templates");
};
