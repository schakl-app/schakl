/**
 * projects web module (CLAUDE.md §6, §10) — mirrors the API module.
 * Self-registers a nav item and the `projects.company` company panel.
 */
import { registerWebModule } from "$lib/core/registry";
import { t } from "$lib/core/i18n";
import { FolderKanban } from "@lucide/svelte";

import ProjectsPanel from "./ProjectsPanel.svelte";

registerWebModule({
  name: "projects",
  nav: [
    {
      key: "projects",
      href: "/projects",
      label: () => t("nav.projects"),
      module: "projects",
      group: "relations",
      icon: FolderKanban,
      position: 25,
    },
  ],
  companyPanels: [
    {
      key: "projects.company",
      module: "projects",
      component: ProjectsPanel,
      position: 25,
    },
  ],
});
