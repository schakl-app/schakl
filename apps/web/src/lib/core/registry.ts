/**
 * Web module + nav registry (CLAUDE.md §4, §6) — mirrors the API registry.
 *
 * Each web module self-registers nav items and any `CompanyPanel` components it contributes.
 * The shell renders nav from enabled modules; the company detail page composes their panels —
 * so a new attachable module needs no edits to the shell or the company page.
 */
import type { Component } from "svelte";

import type { ApiClient } from "./api/client";
import { t } from "./i18n";
import { can } from "./permissions";
import type { SessionUser } from "./session";

/**
 * A module's own display name, for the screens that *list* modules rather than navigate to them
 * (Instellingen → Modules, the instance-admin org view, the first-run wizard).
 *
 * It is deliberately not `nav.<name>`: a module need not contribute a nav item at all
 * (`notifications` reaches you through the header bell), and those screens were printing the raw
 * key `nav.notifications` to the user (issue #58). A label belongs to the module; a nav label
 * belongs to the sidebar entry, and a module may have none.
 *
 * The name is the API's — an instance may ship a module this web build doesn't know — so an
 * unlabelled module names itself rather than leaking an i18n key.
 */
export function moduleLabel(name: string): string {
  const key = `module.${name}.label`;
  const label = t(key);
  return label === key ? name : label;
}

export interface NavItem {
  key: string;
  href: string;
  /** Returns the translated label (call a Paraglide accessor inside). */
  label: () => string;
  module: string;
  position?: number;
  /** Sidebar icon (a lucide component); rendered at 18px. */
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  icon?: Component<any>;
  /**
   * Sidebar group key (e.g. "relations"): items sharing a group render as a submenu under
   * one header, labelled by the `nav.group.<key>` i18n key. Ungrouped items stay top-level.
   */
  group?: string;
  /**
   * Hide this item unless the user holds the permission (issue #19). UX, not security: the
   * route it links to is gated server-side, in its `+page.server.ts` and again in the API.
   */
  requiresPermission?: string;
}

export interface CompanyPanelSpec {
  /** Matches the API PanelSpec.key it renders (e.g. "companies.details"). */
  key: string;
  module: string;
  /** `members` is optional context the host already holds (mention candidates, #151) —
   *  a panel that doesn't take the prop simply never reads it. */
  component: Component<{
    companyId: string;
    data: Record<string, unknown>;
    members?: PanelMember[];
  }>;
  position?: number;
}

/** A member as `/api/v1/members/lookup` returns them. Panels print names, never user ids. */
export interface PanelMember {
  user_id: string;
  full_name: string | null;
  email: string;
  /** Effective avatar (#122); rides the members lookup, no per-person fetch. */
  avatar_url?: string | null;
}

/**
 * The lookups a detail page has already fetched, handed down to its panels.
 *
 * A panel that refetched these would be the exact bug `docs/PERFORMANCE.md` names: a second
 * 200-row company fetch to render a name the page is already holding. So the host passes what it
 * has, and a panel that needs none of it ignores the lot. `id`+name shapes only — a panel renders
 * labels and fills pickers, it does not need the records.
 */
export interface EntityPanelLookups {
  members: PanelMember[];
  companies: { id: string; name: string }[];
  projects: { id: string; name: string; company_id?: string | null }[];
  tasks: {
    id: string;
    title: string;
    project_id?: string | null;
    allocated_minutes?: number | null;
  }[];
}

/** What a host page tells a panel about the entity it is hanging off. */
export interface EntityPanelContext {
  entityId: string;
  /**
   * The day the host's aggregate starts counting from — a project's budget-period start (from the
   * API, never recomputed in the browser). `null` means "no lower bound" (a `total` budget).
   * A panel that answers "which records made that number" must count exactly what the number did.
   */
  periodStart: string | null;
}

/**
 * A panel a module contributes to some *other* module's detail page (#43).
 *
 * The company detail view composes `CompanyPanelSpec`s through the API's panel providers, which
 * hand back an opaque dict. This is the other seam: the panel loads through the **typed client**,
 * the way a dashboard widget does, because a panel that pages, counts and links needs its
 * endpoint's types more than it needs a generic envelope.
 *
 * The point is the same either way — a project page renders whatever the enabled modules offer,
 * so a tenant without `time` simply never sees a Uren panel, and no route file imports another
 * module's internals (CLAUDE.md §6).
 *
 * A panel that edits its records posts to the **host page's** form actions: SvelteKit actions
 * live on the page, so the host owns them. Say which ones a panel needs in its own doc comment.
 */
export interface EntityPanelSpec {
  /** Unique panel key, e.g. "time.entries". */
  key: string;
  module: string;
  /** The host entity this attaches to, e.g. "project". */
  entityType: string;
  position?: number;
  /** i18n key for the panel heading. */
  titleKey: string;
  /** Server-side loader; runs inside the host page's `load`, API-only. */
  load: (api: ApiClient, context: EntityPanelContext) => Promise<unknown>;
  component: Component<{
    data: unknown;
    context: EntityPanelContext;
    lookups: EntityPanelLookups;
  }>;
}

/** A widget's grid footprint, so the gallery can show its shape and the layout can honour it. */
export type WidgetSize = "sm" | "md" | "lg";

export interface DashboardWidgetSpec {
  /** Unique widget key, e.g. "time.today". */
  key: string;
  module: string;
  /** Server-side data loader (runs in the dashboard's +page.server.ts, API-only). */
  load: (api: ApiClient) => Promise<unknown>;
  component: Component<{ data: unknown }>;
  position?: number;
  /** Only offered to holders of this permission — its loader calls an endpoint gated on it. */
  requiresPermission?: string;
  // --- gallery metadata (issue #15) -------------------------------------------------------- #
  /** i18n key for the gallery card title. Falls back to `dashboard.widget.<key>`. */
  titleKey?: string;
  /** i18n key for the one-line gallery description. */
  descriptionKey?: string;
  /** Gallery grouping — an i18n key like `dashboard.category.time`. */
  category?: string;
  /** Grid footprint (default `md`), shown in the gallery and applied to the tile. */
  size?: WidgetSize;
  /**
   * A static, **data-free** preview for the gallery card. The gallery must never call `load()`
   * just to draw a thumbnail (that would fire N API calls to open the picker — docs/PERFORMANCE.md),
   * so a widget with no preview renders a generic skeleton instead.
   */
  preview?: Component;
}

/** The gallery card title: the widget's own `titleKey`, else the legacy per-key label. */
export function widgetTitleKey(spec: DashboardWidgetSpec): string {
  return spec.titleKey ?? `dashboard.widget.${spec.key}`;
}

/** One entry on the shared calendar (`/calendar`), normalized across modules. */
export interface CalendarEvent {
  id: string;
  /** Inclusive date-only ISO range (multi-day events span cells). */
  start: string;
  end: string;
  title: string;
  /** Token from the shared label palette (tasks/labels.ts). */
  color: string;
  href?: string;
  /** Tentative events (e.g. pending leave) render muted with a "?" marker. */
  tentative?: boolean;
  /**
   * `"holiday"` renders as a quiet full-width marking rather than a chip: a public holiday is
   * not somebody's absence, it is nobody's working day, and drawing it as one more coloured
   * pill next to three people's leave says the opposite.
   */
  kind?: "event" | "holiday";
  /**
   * UTC instants for *timed* events (#155): the day/week time grid positions blocks by
   * these, rendered in the org timezone. A source that only knows dates leaves them unset
   * and its events land in the pinned all-day row — nothing changes for date-only feeds.
   */
  startsAt?: string;
  endsAt?: string;
  /** The contributing source's `key` — required for drag-to-reschedule, so the page's
   *  `moveEvent` action can dispatch the drop back to the module that owns the event (#106). */
  sourceKey?: string;
  /** Whether the *viewer* may drag this event to another day. The source decides from the
   *  user it was handed (own event, or a `:any` grant); the API re-checks either way. */
  draggable?: boolean;
}

/** A colleague a source can overlay on the calendar (#188) — the per-person feed roster. */
export interface CalendarPerson {
  id: string;
  name: string;
}

/** What a calendar source's `load` gets to work with. `user` carries the viewer's id +
 *  effective permissions so a source can mark events as its own / draggable (#106). */
export interface CalendarRange {
  from: string;
  to: string;
  locale: string;
  user?: { id: string; permissions: string[] } | null;
  /**
   * The colleagues the viewer chose to overlay for *this* source (#188), from the feeds menu.
   * A source that offers a `people` roster loads those users' items in addition to the viewer's
   * own; a source without one ignores this.
   */
  people?: string[];
}

export interface CalendarSourceSpec {
  /** Unique source key, e.g. "leave.team". */
  key: string;
  module: string;
  /** i18n key naming this feed in the visibility menu / legend (#121). */
  labelKey: string;
  /** Legend swatch — a label colour token (core/ui/colors), matching the feed's chips. */
  color: string;
  /** Server-side loader (runs in the calendar's +page.server.ts, API-only). */
  load: (api: ApiClient, range: CalendarRange) => Promise<CalendarEvent[]>;
  /**
   * Reschedule one of this source's events by whole days (#106) — the drop side of
   * drag-to-move. Runs server-side in the calendar's `moveEvent` action; must go through the
   * API, which recomputes hours and re-triggers approval (CLAUDE.md §14, #72). Returns an
   * error i18n key, or null on success.
   */
  move?: (api: ApiClient, args: { id: string; deltaDays: number }) => Promise<string | null>;
  /**
   * The colleagues this viewer may overlay on the calendar (#188). When present, the feeds menu
   * renders a per-person checklist under the source, each person persisted per user; the picked
   * ids arrive back in `range.people` on the next `load`. Returns `[]` when the viewer lacks the
   * permission to see anyone else (a member sees only their own feed).
   */
  people?: (api: ApiClient, range: CalendarRange) => Promise<CalendarPerson[]>;
}

export interface WebModule {
  name: string;
  nav?: NavItem[];
  companyPanels?: CompanyPanelSpec[];
  /** Panels this module hangs off another module's detail page (e.g. Uren on a project). */
  entityPanels?: EntityPanelSpec[];
  dashboardWidgets?: DashboardWidgetSpec[];
  /** Event feeds composed by the shared calendar — Google Calendar plugs in here later (P3). */
  calendarSources?: CalendarSourceSpec[];
}

const _modules = new Map<string, WebModule>();

// Panels core hangs off *every* host entity, independent of which modules are enabled — the
// activity trail is a core capability (issue #67), mirroring the API registry's core panels.
const _coreCompanyPanels: CompanyPanelSpec[] = [];
const _coreEntityPanels: EntityPanelSpec[] = [];

export function registerCoreCompanyPanel(spec: CompanyPanelSpec): void {
  _coreCompanyPanels.push(spec);
}

export function registerCoreEntityPanel(spec: EntityPanelSpec): void {
  _coreEntityPanels.push(spec);
}

export function registerWebModule(mod: WebModule): void {
  _modules.set(mod.name, mod);
}

export function enabledWebModules(enabled: string[]): WebModule[] {
  return enabled.map((name) => _modules.get(name)).filter((m): m is WebModule => Boolean(m));
}

/** One saved sidebar entry (#169): a module nav key, optionally hidden. */
export interface NavPrefItem {
  key: string;
  hidden?: boolean;
}

export function navItemsFor(
  enabled: string[],
  user?: SessionUser | null,
  pref?: NavPrefItem[] | null,
): NavItem[] {
  const base = enabledWebModules(enabled)
    .flatMap((m) => m.nav ?? [])
    .filter((item) => !item.requiresPermission || can(user, item.requiresPermission));
  if (!pref || pref.length === 0) {
    return base.sort((a, b) => (a.position ?? 100) - (b.position ?? 100));
  }
  // A saved layout (#169) orders and hides *module* items; anything the pref doesn't know —
  // a module enabled after it was saved — falls back to its declared position, after the
  // ordered ones, so a new nav item always appears.
  const order = new Map(pref.map((item, index) => [item.key, index]));
  const hidden = new Set(pref.filter((item) => item.hidden).map((item) => item.key));
  return base
    .filter((item) => !hidden.has(item.key))
    .sort((a, b) => {
      const ia = order.get(a.key);
      const ib = order.get(b.key);
      if (ia !== undefined && ib !== undefined) return ia - ib;
      if (ia !== undefined || ib !== undefined) return ia !== undefined ? -1 : 1;
      return (a.position ?? 100) - (b.position ?? 100);
    });
}

export function companyPanelComponent(
  enabled: string[],
  key: string,
): CompanyPanelSpec | undefined {
  return [
    ..._coreCompanyPanels,
    ...enabledWebModules(enabled).flatMap((m) => m.companyPanels ?? []),
  ].find((p) => p.key === key);
}

/** The panels attached to `entityType`, in display order — core's plus the enabled modules'. */
export function entityPanelsFor(enabled: string[], entityType: string): EntityPanelSpec[] {
  return [..._coreEntityPanels, ...enabledWebModules(enabled).flatMap((m) => m.entityPanels ?? [])]
    .filter((p) => p.entityType === entityType)
    .sort((a, b) => (a.position ?? 100) - (b.position ?? 100));
}

export function dashboardWidgetsFor(
  enabled: string[],
  user?: SessionUser | null,
): DashboardWidgetSpec[] {
  return enabledWebModules(enabled)
    .flatMap((m) => m.dashboardWidgets ?? [])
    .filter((w) => !w.requiresPermission || can(user, w.requiresPermission))
    .sort((a, b) => (a.position ?? 100) - (b.position ?? 100));
}

export function calendarSourcesFor(enabled: string[]): CalendarSourceSpec[] {
  return enabledWebModules(enabled).flatMap((m) => m.calendarSources ?? []);
}
