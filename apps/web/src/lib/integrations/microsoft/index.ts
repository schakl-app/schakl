/**
 * microsoft web module (CLAUDE.md §6a, docs/MICROSOFT.md) — mirrors the licensed API integration.
 *
 * No nav item: the integration surfaces live inside existing screens — the Agenda (the Outlook
 * calendar source below), company/project/task detail (the OneDrive panels), the personal account
 * page (the connect card), the interactions timeline (the mailbox refresh button and the message
 * picker) and Instellingen → Microsoft 365 (org config). The same seams the Google integration
 * registers onto, so a viewer who connected both sees two calendar feeds, two folder panels and
 * one review queue.
 */
import { t } from "$lib/core/i18n";
import { registerWebModule, type EntityPanelSpec } from "$lib/core/registry";

import OneDriveCompanyPanel from "./OneDriveCompanyPanel.svelte";
import OneDriveEntityPanel from "./OneDriveEntityPanel.svelte";

/** OneDrive sits beside Drive, one step after it: reference material under the work. */
const ONEDRIVE_POSITION = 56;

// Task links roll up onto the project panel, so the project load asks with rollup.
const oneDriveEntityPanels: EntityPanelSpec[] = (
  [
    ["project", true],
    ["task", false],
  ] as const
).map(([entityType, rollup]) => ({
  key: `microsoft.onedrive.${entityType}`,
  module: "microsoft",
  entityType,
  titleKey: "microsoft.onedrive.panel.title",
  position: ONEDRIVE_POSITION,
  // Reference, not work (#404): a OneDrive row is a link into somebody else's system.
  prominence: "register" as const,
  requiresPermission: "microsoft.onedrive.read",
  load: async (api, { entityId }) => {
    // The links and the provisioning readiness in one fan (#444's rule): a create button drawn
    // off the caller's permission alone is a control that can only 409.
    const [links, state] = await Promise.all([
      api.GET("/api/v1/microsoft/onedrive/links", {
        params: { query: { entity_type: entityType, entity_id: entityId, rollup } },
      }),
      api.GET("/api/v1/microsoft/onedrive/state", {
        params: { query: { entity_type: entityType, entity_id: entityId } },
      }),
    ]);
    return { links: links.data ?? [], entityType, state: state.data ?? null };
  },
  component: OneDriveEntityPanel,
}));

registerWebModule({
  name: "microsoft",
  // A conversation with somebody else's service (CLAUDE.md §6a).
  kind: "integration",
  companyPanels: [
    {
      key: "microsoft.onedrive.company",
      module: "microsoft",
      component: OneDriveCompanyPanel,
      position: ONEDRIVE_POSITION,
    },
  ],
  entityPanels: oneDriveEntityPanels,
  calendarSources: [
    {
      // docs/MICROSOFT.md §4: the viewer's own Outlook events, served from the API's local
      // cache (one cheap DB read — the Agenda never talks to Graph live). A viewer who never
      // connected simply gets an empty feed.
      key: "microsoft.calendar",
      module: "microsoft",
      labelKey: "microsoft.calendar.source_label",
      color: "indigo",
      load: async (api, { from, to, color, personColors, hiddenPeople }) => {
        const { data } = await api.GET("/api/v1/microsoft/calendar/events", {
          params: { query: { date_from: from, date_to: to } },
        });
        // Per-calendar colour and hide, riding the per-person split machinery (#281): the
        // "person" here is a calendar, keyed by its Graph id.
        const hidden = new Set(hiddenPeople ?? []);
        return (data ?? [])
          .filter((event) => !hidden.has(event.calendar_id ?? "primary"))
          .map((event) => ({
            id: `mscal-${event.id}`,
            start: event.start,
            end: event.end,
            title: event.title || t("microsoft.calendar.untitled"),
            color: personColors?.[event.calendar_id ?? "primary"] ?? color ?? "indigo",
            href: event.html_link ?? undefined,
            tentative: event.tentative,
            cancelled: event.cancelled,
            sourceKey: "microsoft.calendar",
            startsAt: event.starts_at ?? undefined,
            endsAt: event.ends_at ?? undefined,
          }));
      },
      // One split row per synced calendar — drawn only when there is a second calendar to
      // split on. Database only: the feeds menu opens on every Agenda visit.
      splitPeople: async (api) => {
        const { data } = await api.GET("/api/v1/microsoft/calendar/channels");
        const rows = data ?? [];
        if (rows.length <= 1) return [];
        return rows.map((row) => ({
          id: row.calendar_id,
          name: row.primary ? t("microsoft.calendar.primary") : row.summary || row.calendar_id,
        }));
      },
    },
  ],
});
