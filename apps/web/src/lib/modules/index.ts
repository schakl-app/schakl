/**
 * Loads every web module and integration so it self-registers into the registry (CLAUDE.md §6).
 * Imported from both server and client hooks. Add one here as it's built.
 *
 * The two lists mirror the two packages (`$lib/modules`, `$lib/integrations`) and the API's own
 * split (CLAUDE.md §6a): a module is a capability of ours, an integration is a conversation with
 * somebody else's service. They register through the same `registerWebModule` and differ only in
 * the `kind` they declare — which is what the settings screens group on, and what stops the
 * question "is Cloudflare a module?" from having two answers depending on which screen you are
 * looking at.
 */
import "$lib/core/activity"; // core capability: the activity panel on every auditable entity (#67)

// --- Modules: what schakl itself does ------------------------------------- //
import "./companies";
import "./contacts";
import "./tasks";
import "./projects";
import "./subscriptions";
import "./invoicing";
import "./time";
import "./leave";
import "./notifications";
import "./domains";
import "./hosting";
import "./websites";
import "./interactions";
import "./marketing";
import "./reporting";
import "./portal";

// --- Integrations: what schakl talks to ----------------------------------- //
import "$lib/integrations/google";
import "$lib/integrations/google_ads";
import "$lib/integrations/google_tag_manager";
import "$lib/integrations/cloudflare";
import "$lib/integrations/uptime";
import "$lib/integrations/wordpress";
import "$lib/integrations/oxxa";
import "$lib/integrations/mollie";
import "$lib/integrations/snelstart";
import "$lib/integrations/timeon";
