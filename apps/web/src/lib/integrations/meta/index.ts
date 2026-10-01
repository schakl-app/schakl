/**
 * meta web module: the social planner in the Marketing group, and planned posts on the agenda.
 * Self-registers on import via the `lib/modules` barrel.
 *
 * The nav item is called **Social**, not Meta: it is where a client's posts are planned, and
 * what it is called should still be right the day a second network is added. The vendor's name
 * belongs on the settings screen that holds the vendor's credential, and nowhere in a
 * white-label product's main menu (#389).
 *
 * **No company panel** (#411): integration cards came off the client hub on purpose. A
 * client's planned posts are one filter away (`/marketing/social?company=…`).
 */
import Share2 from "@lucide/svelte/icons/share-2";

import { apiErrorKey } from "$lib/core/errors";
import { t } from "$lib/core/i18n";
import { isoAddDays } from "$lib/core/isodate";
import { hasPermission } from "$lib/core/permissions";
import { type CalendarEvent, registerWebModule } from "$lib/core/registry";
import { localDayTime } from "$lib/core/wallclock";

import { channelLabel } from "./format";
import MetaCompanyPanel from "./MetaCompanyPanel.svelte";

/** The post statuses the agenda draws: what will go out, what is going out, what went out. */
const AGENDA_STATUSES = "scheduled,publishing,published,partial,failed";

registerWebModule({
  name: "meta",
  // A conversation with somebody else's service (CLAUDE.md §6a).
  kind: "integration",
  nav: [
    {
      key: "social",
      href: "/marketing/social",
      label: () => t("nav.social"),
      module: "meta",
      group: "marketing",
      icon: Share2,
      // After Tag Manager (47): the dashboard, the advertising, what measures it, what is said.
      position: 48,
      requiresPermission: "meta.asset.read",
    },
  ],
  companyPanels: [
    {
      key: "meta.company",
      module: "meta",
      component: MetaCompanyPanel,
      position: 46,
      // Nothing linked for this client yet: the chip goes to where a channel is linked, for
      // somebody who may link one. Anybody else gets the chip that unfolds in place, never
      // a link to a screen that sends them back (#253).
      emptyHref: () => "/marketing/social/channels",
      emptyRequiresPermission: "meta.settings.manage",
    },
  ],
  calendarSources: [
    {
      // Planned and published posts. Its own feed, so somebody planning work can switch a
      // content calendar off without losing anything else — and timed, so a post lands at its
      // hour on the day and week grid.
      key: "meta.posts",
      module: "meta",
      labelKey: "meta.calendar.posts",
      color: "violet",
      load: async (api, { from, to, user, color }): Promise<CalendarEvent[]> => {
        if (!hasPermission(user?.permissions, "meta.asset.read")) return [];
        const mayMove = hasPermission(user?.permissions, "meta.post.publish");
        const { data } = await api.GET("/api/v1/meta-business/posts", {
          params: {
            query: {
              status: AGENDA_STATUSES,
              date_from: from,
              date_to: to,
              limit: 200,
              offset: 0,
              count: false,
            },
          },
        });
        return (data?.items ?? []).flatMap((post) => {
          const when = post.published_at ?? post.scheduled_at;
          if (!when) return [];
          const { day } = localDayTime(when);
          const channels = [...new Set(post.targets.map((tg) => channelLabel(tg.channel)))];
          const who = post.company_name ? `${post.company_name}: ` : "";
          return [
            {
              id: post.id,
              start: day,
              end: day,
              title: `${who}${post.title || channels.join(" + ")}`,
              // A failed post is red whatever the feed's colour: the one chip on the agenda
              // that is a fault is the one that must not blend in.
              color:
                post.status === "failed" || post.status === "partial" ? "red" : (color ?? "violet"),
              href: `/marketing/social/${post.id}`,
              startsAt: when,
              // A post is a moment, not a span; half an hour is what makes it a block a finger
              // can hit on the time grid.
              endsAt: new Date(new Date(when).getTime() + 30 * 60_000).toISOString(),
              sourceKey: "meta.posts",
              // Only a post still waiting for its time can move, and only for a viewer who may
              // schedule one. The API re-checks either way.
              draggable: mayMove && post.status === "scheduled",
            },
          ];
        });
      },
      move: async (api, { id, deltaDays }) => {
        // A day-move keeps the hour. The clock is sent **naked** — the API reads a naive time
        // on the org's own calendar (CLAUDE.md §8), so nine o'clock stays nine o'clock across
        // the night the clocks change.
        const current = await api.GET("/api/v1/meta-business/posts/{post_id}", {
          params: { path: { post_id: id } },
        });
        if (current.error) return apiErrorKey(current.error).key;
        if (!current.data.scheduled_at) return "meta.issue.no_time";
        const { day, time } = localDayTime(current.data.scheduled_at);
        const { error } = await api.PATCH("/api/v1/meta-business/posts/{post_id}", {
          params: { path: { post_id: id } },
          body: { scheduled_at: `${isoAddDays(day, deltaDays)}T${time}:00` },
        });
        return error ? apiErrorKey(error).key : null;
      },
    },
  ],
});
