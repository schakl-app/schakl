/**
 * meetings web module (CLAUDE.md §6) — mirrors the API module: a recorded meeting into a
 * transcript, minutes and tasks.
 *
 * Self-registers on import via the `lib/modules` barrel. The nav item, the client-hub panel; the
 * screens live under `routes/(app)/meetings/`.
 */
import Mic from "@lucide/svelte/icons/mic";

import { t } from "$lib/core/i18n";
import { registerWebModule } from "$lib/core/registry";

import MeetingsCompanyPanel from "./MeetingsCompanyPanel.svelte";

registerWebModule({
  name: "meetings",
  nav: [
    {
      key: "meetings",
      href: "/meetings",
      label: () => t("nav.meetings"),
      module: "meetings",
      icon: Mic,
      // Beside Interacties (27): a meeting is a contact moment with its words kept.
      position: 28,
      requiresPermission: "meetings.meeting.read",
    },
  ],
  companyPanels: [
    {
      key: "meetings.company",
      module: "meetings",
      component: MeetingsCompanyPanel,
      position: 38,
      // Nothing here yet folds into the hub's one ＋ strip (#364); the chip opens the recorder
      // with the client already on the form.
      emptyHref: (id: string) => `/meetings/new?company=${id}`,
    },
  ],
});

export { MeetingsCompanyPanel };
