/**
 * google web module (CLAUDE.md §6, issue #22) — mirrors the licensed API module.
 *
 * No nav item: the integration surfaces live inside existing screens — the Agenda (calendar
 * source below), company/project/task detail (Drive panels), the personal account page (the
 * connect card) and Instellingen → Google (org config).
 */
import { t } from "$lib/core/i18n";
import { registerWebModule, type EntityPanelSpec } from "$lib/core/registry";

import DriveCompanyPanel from "./DriveCompanyPanel.svelte";
import DriveEntityPanel from "./DriveEntityPanel.svelte";

/** Drive sits with the working surfaces: above contactmomenten (60), under time (10-40). */
const DRIVE_POSITION = 55;

// Task links roll up onto the project panel (#21), so the project load asks with rollup.
const driveEntityPanels: EntityPanelSpec[] = (
  [
    ["project", true],
    ["task", false],
  ] as const
).map(([entityType, rollup]) => ({
  key: `google.drive.${entityType}`,
  module: "google",
  entityType,
  titleKey: "google.drive.panel.title",
  position: DRIVE_POSITION,
  load: async (api, { entityId }) => {
    const { data } = await api.GET("/api/v1/google/drive/links", {
      params: { query: { entity_type: entityType, entity_id: entityId, rollup } },
    });
    return { links: data ?? [], entityType };
  },
  component: DriveEntityPanel,
}));

registerWebModule({
  name: "google",
  companyPanels: [
    {
      key: "google.drive.company",
      module: "google",
      component: DriveCompanyPanel,
      position: DRIVE_POSITION,
    },
  ],
  entityPanels: driveEntityPanels,
  calendarSources: [
    {
      // The docs/GOOGLE.md §4 seam: the viewer's own Google events, served from the API's
      // local cache (one cheap DB read — the Agenda never talks to Google live). A viewer
      // who never connected simply gets an empty feed.
      key: "google.calendar",
      module: "google",
      labelKey: "google.calendar.source_label",
      color: "blue",
      load: async (api, { from, to, color }) => {
        const { data } = await api.GET("/api/v1/google/calendar/events", {
          params: { query: { date_from: from, date_to: to } },
        });
        return (data ?? []).map((event) => ({
          id: `gcal-${event.id}`,
          start: event.start,
          end: event.end,
          title: event.title || t("google.calendar.untitled"),
          color: color ?? "blue",
          href: event.html_link ?? undefined,
          tentative: event.tentative,
          sourceKey: "google.calendar",
          // Timed events position on the day/week grid (#155); all-day ones stay chips.
          startsAt: event.starts_at ?? undefined,
          endsAt: event.ends_at ?? undefined,
        }));
      },
    },
  ],
});
