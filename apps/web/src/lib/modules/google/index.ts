/**
 * google web module (CLAUDE.md §6, issue #22) — mirrors the licensed API module.
 *
 * No nav item: the integration surfaces live inside existing screens — the Agenda (calendar
 * source below), company/project/task detail (Drive panels), the personal account page (the
 * connect card) and Instellingen → Google (org config).
 */
import { t } from "$lib/core/i18n";
import { registerWebModule } from "$lib/core/registry";

registerWebModule({
  name: "google",
  calendarSources: [
    {
      // The docs/GOOGLE.md §4 seam: the viewer's own Google events, served from the API's
      // local cache (one cheap DB read — the Agenda never talks to Google live). A viewer
      // who never connected simply gets an empty feed.
      key: "google.calendar",
      module: "google",
      labelKey: "google.calendar.source_label",
      color: "blue",
      load: async (api, { from, to }) => {
        const { data } = await api.GET("/api/v1/google/calendar/events", {
          params: { query: { date_from: from, date_to: to } },
        });
        return (data ?? []).map((event) => ({
          id: `gcal-${event.id}`,
          start: event.start,
          end: event.end,
          title: event.title || t("google.calendar.untitled"),
          color: "blue",
          href: event.html_link ?? undefined,
          tentative: event.tentative,
          sourceKey: "google.calendar",
        }));
      },
    },
  ],
});
