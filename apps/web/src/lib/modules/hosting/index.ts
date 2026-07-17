import { registerWebModule } from "$lib/core/registry";

// Hosting contributes no main-nav item and no company panel (owner feedback): agencies
// reuse the same hosting, so the list is administered under Instellingen → Hosting, and the
// client page shows the client's *websites* (each naming its hosting) instead.
registerWebModule({
  name: "hosting",
});
