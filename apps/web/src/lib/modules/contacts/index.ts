/**
 * contacts web module (CLAUDE.md §6) — mirrors the API module.
 * Self-registers its nav item and the `contacts.company` company panel.
 */
import { registerWebModule } from "$lib/core/registry";
import { t } from "$lib/core/i18n";
import { Users } from "@lucide/svelte";

import ContactsPanel from "./ContactsPanel.svelte";

registerWebModule({
  name: "contacts",
  nav: [
    {
      key: "contacts",
      href: "/contacts",
      label: () => t("nav.contacts"),
      module: "contacts",
      // Its own top-level item (issue #91) — no longer under the "relations" group.
      icon: Users,
      position: 20,
      requiresPermission: "contacts.contact.read",
    },
  ],
  companyPanels: [
    {
      key: "contacts.company",
      module: "contacts",
      component: ContactsPanel,
      position: 20,
    },
  ],
});
