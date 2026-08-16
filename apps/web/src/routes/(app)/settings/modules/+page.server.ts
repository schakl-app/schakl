import { enablementActions, enablementData } from "$lib/core/settings/enablement.server";

import type { Actions, PageServerLoad } from "./$types";

/** Shared with Instellingen → Integraties: two screens editing one `enabled_modules` list (#378). */
export const load: PageServerLoad = (event) => enablementData(event);

export const actions: Actions = enablementActions;
