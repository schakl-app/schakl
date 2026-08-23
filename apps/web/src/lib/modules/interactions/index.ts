/**
 * interactions web module (CLAUDE.md §6, issue #22) — contactmomenten, mirrors the API module.
 *
 * The timeline lives on the records it belongs to (company/project/contact/task panels), and
 * since #168 also on its own cross-cutting Interacties page — nav item below. Host pages spread
 * `interactionActions` (./actions.server) into their form actions — that is the whole contract.
 */
import { MessagesSquare } from "@lucide/svelte";

import { registerWebModule, type EntityPanelSpec } from "$lib/core/registry";
import { t } from "$lib/core/i18n";

import InteractionsCompanyPanel from "./InteractionsCompanyPanel.svelte";
import InteractionsEntityPanel from "./InteractionsEntityPanel.svelte";
import InteractionsPendingWidget from "./InteractionsPendingWidget.svelte";

/** Mirrors `interactions/panels.py::PANEL_LIMIT` — a panel is a summary, not a log. */
const PANEL_LIMIT = 8;

/** Between the working surfaces and the activity trail (90); drive links slot in at 55. */
const POSITION = 60;

const ENTITY_FIELDS = {
  project: "project_id",
  contact: "contact_id",
  task: "task_id",
} as const;

/** On a project the communication timeline is daily-use: right after Uren (10), above the
 *  subscriptions (20) and Drive (55) reference panels. Contact/task pages keep the default. */
const ENTITY_POSITIONS: Partial<Record<keyof typeof ENTITY_FIELDS, number>> = {
  project: 15,
};

/** The hosts where contactmomenten are a register rather than the working surface (#404). */
const REGISTER_ON = new Set<string>(["task"]);

const entityPanels: EntityPanelSpec[] = Object.entries(ENTITY_FIELDS).map(
  ([entityType, field]) => ({
    key: `interactions.${entityType}`,
    module: "interactions",
    entityType,
    titleKey: "interactions.panel.title",
    position: ENTITY_POSITIONS[entityType as keyof typeof ENTITY_FIELDS] ?? POSITION,
    // Per host (#404), for the reason `ENTITY_POSITIONS` above already gives: on a project the
    // communication timeline is daily use, and on a task it is a record of what was said about
    // one piece of work — reference material, under a rule rather than in a box of its own.
    prominence: REGISTER_ON.has(entityType) ? ("register" as const) : ("primary" as const),
    // The key `GET /api/v1/interactions` declares — the same one the nav item above carries.
    // A contact page composed this panel for everyone, so a member without it read an empty
    // Contactmomenten block with a create control beside the heading.
    requiresPermission: "interactions.interaction.read",
    load: async (api, { entityId }) => {
      // A project's communication is its own plus its tasks' (#147); each rolled-up row
      // carries a task chip so the reader sees where it lives.
      const include = entityType === "project" ? "tasks" : null;
      const { data } = await api.GET("/api/v1/interactions", {
        params: {
          query: {
            [field]: entityId,
            limit: PANEL_LIMIT,
            offset: 0,
            ...(include ? { include } : {}),
          },
        },
      });
      return {
        items: data?.items ?? [],
        total: data?.total ?? 0,
        entityField: field,
        // Carried, not re-derived: the panel's "alles bekijken" link (#323) must reach a list
        // filtered exactly the way this read was, or it answers fewer rows than it promised.
        include,
      };
    },
    component: InteractionsEntityPanel,
  }),
);

registerWebModule({
  name: "interactions",
  nav: [
    {
      // Cross-cutting like Contactpersonen, not owned by one group (#168, after #117's order).
      key: "interactions",
      href: "/interactions",
      label: () => t("nav.interactions"),
      module: "interactions",
      icon: MessagesSquare,
      position: 27,
      requiresPermission: "interactions.interaction.read",
    },
  ],
  companyPanels: [
    {
      key: "interactions.company",
      module: "interactions",
      component: InteractionsCompanyPanel,
      position: POSITION,
      // Nothing here yet folds into the hub's one ＋ strip (#364).
      emptyHref: (id: string) => `/interactions?company_id=${id}`,
    },
  ],
  entityPanels,
  dashboardWidgets: [
    {
      key: "interactions.pending_email",
      module: "interactions",
      position: 15,
      requiresPermission: "interactions.interaction.read",
      descriptionKey: "dashboard.widget_desc.interactions.pending_email",
      category: "dashboard.category.review",
      size: "md",
      // Strictly the viewer's own mailbox queue (`mine`) — review is owner-only by design.
      load: (api) =>
        api
          .GET("/api/v1/interactions", {
            params: { query: { status: "pending", mine: true, limit: 5 } },
          })
          .then((r) => ({ items: r.data?.items ?? [], total: r.data?.total ?? 0 })),
      component: InteractionsPendingWidget,
    },
  ],
});
