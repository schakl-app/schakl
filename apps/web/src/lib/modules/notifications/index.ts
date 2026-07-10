/**
 * notifications web module (CLAUDE.md §6, issue #16) — mirrors the API module.
 *
 * It contributes **no nav item**: the bell in the header is the way in, and the sidebar is not
 * where you check whether anything happened. Registering it here is still what makes the tenant's
 * `enabled_modules` gate the shell's bell and, later, the activity panels other pages host.
 */
import { registerWebModule } from "$lib/core/registry";

registerWebModule({
  name: "notifications",
});
