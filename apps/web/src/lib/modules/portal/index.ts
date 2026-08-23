/**
 * portal web module (CLAUDE.md §6) — mirrors the API module.
 *
 * It contributes no nav item and no company panel: the portal is not a place you go, it is a
 * control on the record of the person you are giving a login to. What it registers is its
 * *existence*, so Instellingen → Modules can list and label it (`module.portal.label`) and the
 * card can gate on `enabledModules.includes("portal")` like every other module's surface.
 */
import { registerWebModule } from "$lib/core/registry";

registerWebModule({ name: "portal" });

export { default as PortalCard } from "./PortalCard.svelte";
export { default as PortalLoginsSection } from "./PortalLoginsSection.svelte";
export type { PortalCardData, PortalLoginRow, PortalLoginState, PortalRegisterData } from "./types";
