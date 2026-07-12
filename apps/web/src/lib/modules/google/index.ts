/**
 * google web module (CLAUDE.md §6, issue #22) — mirrors the licensed API module.
 *
 * No nav item: the integration surfaces live inside existing screens — the Agenda (calendar
 * source), company/project/task detail (Drive panels), the personal account page (the connect
 * card) and Instellingen → Google (org config). The calendar source and Drive panels register
 * here as their slices land.
 */
import { registerWebModule } from "$lib/core/registry";

registerWebModule({
  name: "google",
});
