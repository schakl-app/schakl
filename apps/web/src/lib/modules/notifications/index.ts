/**
 * notifications web module (CLAUDE.md §6, issue #16) — mirrors the API module.
 *
 * It contributes **no nav item**: the bell in the header is the way in, and the sidebar is not
 * where you check whether anything happened.
 *
 * It no longer contributes the per-record activity panel either — that is a core capability now
 * (issue #67, `$lib/core/activity`), a real audit trail rather than the notifiable subset this
 * module logs. Notifications keeps the bell, the inbox and the delivery preferences.
 */
import { registerWebModule } from "$lib/core/registry";

registerWebModule({
  name: "notifications",
});
