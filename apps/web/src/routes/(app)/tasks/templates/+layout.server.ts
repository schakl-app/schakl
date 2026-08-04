import { settingsShellData } from "$lib/core/settings/shell.server";

import type { LayoutServerLoad } from "./$types";

/** Taaksjablonen is an Instellingen screen administered on the tasks page (#229) — it carries the
 *  section rail itself, so what the rail needs is resolved here. */
export const load: LayoutServerLoad = (event) => settingsShellData(event);
