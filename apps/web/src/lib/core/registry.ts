/**
 * Web module + nav registry (CLAUDE.md §4, §6) — mirrors the API registry.
 *
 * Each web module self-registers nav items and any `CompanyPanel` components it contributes.
 * The shell renders nav from enabled modules; the company detail page composes their panels —
 * so a new attachable module needs no edits to the shell or the company page.
 */
import type { Component } from "svelte";

import { getLocale } from "$lib/paraglide/runtime";

import type { CustomFieldDefinition } from "./customfields/types";

import type { ApiClient } from "./api/client";
import { t } from "./i18n";
import { can, type PermissionScope } from "./permissions";
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

/**
 * One line saying what a module or integration *is*, for the two screens that ask a reader to
 * decide whether to run it (issue #378).
 *
 * Instellingen → Modules listed twenty-six names against twenty-six checkboxes. "HR",
 * "Klantportaal", "Uptime" are not self-explanatory to the person who has to decide, and the
 * consequence was that nobody switched anything on or off deliberately — the screen could be read
 * only by someone who already knew the product, which is the audience least likely to be reading it.
 *
 * Empty rather than the raw key for a module this build has no copy for, exactly as `moduleLabel`
 * falls back to the name: a missing description costs a line of prose, and printing
 * `module.foo.description` at a user costs their trust in every other line on the screen.
 */
export function moduleDescription(name: string): string {
  const key = `module.${name}.description`;
  const text = t(key);
  return text === key ? "" : text;
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
  /**
   * The scope that permission is required at — omitted means the route's floor ("holds it at
   * some scope"), which is right for almost every item.
   *
   * It exists because a scoped key can gate two very different screens (#266): the invoice
   * list is a document read an `:own` holder should see, and *Nog te factureren* — the org's
   * whole unbilled backlog, employee rates included — sits on the same key at `:any`. Without
   * a scope here the sidebar offers a client a link that always 403s, which is #253's rule
   * ("a link that always refuses is a broken control") in the one place it could not be
   * expressed. Mirrors `require_permission(key, scope)` on the route it points at.
   */
  requiresScope?: PermissionScope;
  /**
   * What the item is called on a **client's** sidebar, where the staff word is wrong rather
   * than merely unfamiliar: the agency's "Klanten" is, to the person signed in, their own
   * companies. Omitted means the one label serves both audiences. Picked by `navItemsFor` on
   * `user.isPortal` — an audience is a layout question (#373), and the tenant's own nav
   * rename still wins over both.
   */
  portalLabel?: () => string;
}

export interface CompanyPanelSpec {
  /** Matches the API PanelSpec.key it renders (e.g. "companies.details"). */
  key: string;
  module: string;
  /** `members`, `definitions` and `locale` are optional context the host already holds
   *  (mention candidates #151; the tenant's custom-field definitions #364) — a panel that
   *  doesn't take the prop simply never reads it. */
  component: Component<{
    companyId: string;
    data: Record<string, unknown>;
    members?: PanelMember[];
    definitions?: CustomFieldDefinition[];
    locale?: string;
    /** The heading the host would draw; passed so an `ownsHeader` panel can draw it itself. */
    title?: string;
    onedit?: () => void;
  }>;
  position?: number;
  /**
   * This panel draws its own heading row (`PanelHeader`), so the host draws none (#364).
   *
   * A panel with a control beside its title otherwise had nowhere to put it, because the host
   * owns the `<h2>` — so it pushed a button row *underneath* the heading and the card opened
   * with a band of empty space and one floating control in it.
   */
  ownsHeader?: boolean;
  /**
   * What this module offers when the client has nothing here yet (#364).
   *
   * A module with nothing to show does not earn a heading, a border and 100 px — the API says
   * `empty: true` and the hub folds it into one "nog niets vastgelegd" strip of ＋ chips. The
   * chip needs somewhere to go, and where is a *routing* question the API may not answer, so it
   * lives here beside the component that draws the full panel.
   *
   * A chip with no `emptyHref` is drawn as a plain label: still one line in a strip instead of a
   * card, which is the win, and never a control that goes nowhere (#253).
   */
  emptyHref?: (companyId: string) => string;
  /** Overrides the chip's label; defaults to the panel's own `title_key`. */
  emptyLabelKey?: string;
}

/** A member as `/api/v1/members/lookup` returns them. Panels print names, never user ids. */
export interface PanelMember {
  user_id: string;
  full_name: string | null;
  email: string | null;
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
 *
 * **The record whose page this is must always be in here** (#363). These lists are the page's
 * pickers, and a picker is a capped list — `limit: 200`, unsorted. A panel that answers a
 * question *about the host record* by looking it up among them is asking a question the page
 * already has the answer to, and getting `undefined` the moment the tenant outgrows the cap. The
 * host merges its own record in; the cost is one array spread.
 */
export interface EntityPanelLookups {
  members: PanelMember[];
  companies: { id: string; name: string }[];
  projects: { id: string; name: string; company_id?: string | null }[];
  tasks: {
    id: string;
    title: string;
    project_id?: string | null;
    /**
     * The task's *own* client. A task carries `company_id` independently of `project_id` — one
     * attached straight to a client has no project to walk through — so a panel that needs the
     * client reads it here rather than inferring it from a project it may not have (#363).
     */
    company_id?: string | null;
    allocated_minutes?: number | null;
    status?: string | null;
    due_date?: string | null;
  }[];
  /**
   * The tenant's task-status vocabulary (#62) — what a picker needs to tell an open task from a
   * finished one. Optional: a host page that draws no task picker (an invoice, a domain) has no
   * reason to load it, and a panel that wants it treats an absent list as "offer everything"
   * rather than as "everything is open".
   */
  taskStatuses?: { key: string; name: string; is_terminal: boolean }[];
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
  /**
   * Whose surface this panel's subject is — the mirror of the API's `PanelSpec.audience`.
   *
   * `requiresPermission` answers "may this viewer read these rows"; this answers "is this thing
   * a client surface at all", and they come apart on exactly the panels a client legitimately
   * holds the key to. The activity trail is the case that named it: the seeded `client` role
   * holds `activity.read`, so a client's project, contact, domain and website pages each drew
   * an "Activiteit" heading over *Nog geen activiteit* — the API blanks the feed for them, and
   * a heading over what the API blanks is the screen answering a question the client should not
   * be holding (docs/PORTAL.md, the #446–#449 rule).
   *
   * `everyone` is the default, so a panel that has not thought about it draws exactly what it
   * drew before.
   */
  audience?: "everyone" | "staff";
  /**
   * What this panel *is* on this host (#404) — the same question the API already answers for a
   * company panel (`PanelSpec.prominence`, #364), asked on this side because an entity panel is
   * registered in web code and there is no API descriptor to put it on.
   *
   * `primary` is a working surface. `register` is reference material: correct, occasionally
   * consulted, never news — a host draws it under a hairline rule rather than as a bordered box
   * competing with the work above it (`Card kind="register"`). It is declared **per host**,
   * because the same panel is not the same thing everywhere: contactmomenten on a *contact* is
   * the daily surface, and on a task it is a record of what was said about it.
   *
   * Defaulting to `primary` is deliberate: an omission draws exactly what it drew before, so a
   * host that has not adopted the distinction yet is unchanged rather than quietly demoted.
   */
  prominence?: "primary" | "register";
  /** i18n key for the panel heading. */
  titleKey: string;
  /**
   * The permission `load` calls behind. Nav items and dashboard widgets have always declared
   * this; panels did not, and a host page composed every enabled module's panel for every
   * viewer — so a member without `interactions.interaction.read` got an empty Contactmomenten
   * block on each contact, project and task, with its "＋ nieuw" beside the heading and a
   * wasted 403 round-trip behind it. A panel that renders nothing but a heading is a screen
   * lying about what the visitor may do (docs/UX.md), and the load it skips is free.
   *
   * Omit it only where the endpoint needs no permission, or where the panel deliberately draws
   * its own refusal state because that state is worth telling apart from an empty one — `oxxa`
   * distinguishes "you may not look" from "there is no register account yet", and only the
   * second is fixed by adding a credential.
   */
  requiresPermission?: string;
  /** The scope that permission is required at — omitted means "holds it at some scope". */
  requiresScope?: PermissionScope;
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

/**
 * What the board hands a widget's loader beside the client. The client portal's homepage is
 * one company at a time — a contact linked to two clients switches between them, and every
 * tile must follow the switch, or the board shows one company's marketing over another's
 * invoices. `companyId` is that selection (`null` on the staff board, and for a portal login
 * with nothing attached), so a portal loader filters on it and a staff loader ignores it.
 */
export interface DashboardWidgetContext {
  companyId: string | null;
}

export interface DashboardWidgetSpec {
  /** Unique widget key, e.g. "time.today". */
  key: string;
  module: string;
  /** Server-side data loader (runs in the dashboard's +page.server.ts, API-only). */
  load: (api: ApiClient, context: DashboardWidgetContext) => Promise<unknown>;
  component: Component<{ data: unknown }>;
  position?: number;
  /** Only offered to holders of this permission — its loader calls an endpoint gated on it. */
  requiresPermission?: string;
  /**
   * Which dashboard offers this widget (#254). The staff My Day and the portal homepage are
   * both per-viewing-user widget boards, but their galleries differ: a staff widget may link
   * into routes a portal login cannot open, and the portal's curated-marketing widget is
   * noise on a staff board that already has `/marketing`. Default `"staff"`.
   */
  audience?: "staff" | "portal";
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
   * Called off, but still on the calendar — a Google meeting whose organiser cancelled it.
   * Renders struck through and faded, the way Google itself draws it: the slot being free again
   * is the useful information, and silently removing the chip loses it.
   */
  cancelled?: boolean;
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
  /**
   * The viewer's personal colour override for *this* source (#281) — a label token or a raw hex
   * (`labelChipParts` renders either). When set, the source colours its events with it instead of
   * its own default; a source without an override ignores this.
   */
  color?: string;
  /**
   * Per-colleague colour overrides for a `splitPeople` source (#281), keyed by user id. A leave
   * chip prefers its person's override, then the whole-feed `color`, then the leave-type colour.
   */
  personColors?: Record<string, string>;
  /**
   * The colleagues the viewer hid from a `splitPeople` source (#281), by user id. The source drops
   * their items; unlike `people` (an additive overlay), everyone shows until explicitly hidden.
   */
  hiddenPeople?: string[];
}

export interface CalendarSourceSpec {
  /** Unique source key, e.g. "leave.team". */
  key: string;
  module: string;
  /** i18n key naming this feed in the visibility menu / legend (#121). */
  labelKey: string;
  /** Legend swatch — a label colour token (core/ui/colors), matching the feed's chips. */
  color: string;
  /**
   * Whether a viewer may recolour this feed (#281). Default `true`; set `false` for a feed whose
   * chips ignore colour (holidays render as a quiet dashed band, #47), so the menu shows a static
   * swatch there instead of a pointless picker.
   */
  colorable?: boolean;
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
  /**
   * The colleagues this feed can be *split* into (#281): the feeds menu renders each as its own
   * legend row with an individual colour swatch and a show/hide checkbox, so a viewer can tell
   * three people's leave apart at a glance. Distinct from `people` (an additive overlay picker):
   * a split source already shows everyone, and the split only recolours / hides per person.
   * Returns `[]` when the viewer may not distinguish colleagues (then the feed stays one colour).
   */
  splitPeople?: (api: ApiClient, range: CalendarRange) => Promise<CalendarPerson[]>;
}

/**
 * A connect surface an integration contributes to the marketing picker (#411).
 *
 * The panels pattern, one control over. Tag Manager is something an agency attaches to a client
 * and it is **not** a metrics source (see `modules/marketing/types.ts`), so it cannot ride
 * `ALL_SOURCES` — and the marketing module may not import an integration's component to mount
 * it either (§6: that is the one import direction this tree does not have). So the integration
 * registers its own connect surface here and the picker composes it, exactly as the company hub
 * composes panels it knows nothing about.
 *
 * The permission is **the key the call makes**, never the one the picker is about (#310): this
 * control posts to the contributing module's own route, so it declares that module's key.
 */
export interface MarketingConnectorSpec {
  /** Matches `MarketingConnectionRow.kind` on the payload, e.g. `"gtm"`. */
  kind: string;
  module: string;
  /** Names the group this control sits under. */
  labelKey: string;
  /** The permission the contributed control's own POST declares. */
  requiresPermission: string;
  /**
   * The connect surface itself. `action` is the host page's form action (a panel's control posts
   * to its host, docs/UX.md); `companyId` is empty where the route already names the client;
   * `connectNext` is where an OAuth reconnect should land.
   */
  component: Component<{
    action: string;
    companyId: string;
    connectNext: string;
    error?: string | null;
  }>;
}

/**
 * A capability schakl itself provides, versus a conversation with somebody else's service
 * (CLAUDE.md §6a). The API is the authority — `module_kinds` on `/meta/modules` — and this
 * mirrors it for the screens that classify a name while rendering, where a round trip would be
 * asking the server a question the build already knows the answer to.
 */
export type ModuleKind = "module" | "integration";

export interface WebModule {
  name: string;
  /**
   * Defaults to `"module"`, which is the harmless wrong answer: an integration mislabelled a
   * module lands in the wrong group on one screen, while a module mislabelled an integration
   * claims a credential it does not have and sends the reader looking for one.
   */
  kind?: ModuleKind;
  nav?: NavItem[];
  companyPanels?: CompanyPanelSpec[];
  /** Panels this module hangs off another module's detail page (e.g. Uren on a project). */
  entityPanels?: EntityPanelSpec[];
  dashboardWidgets?: DashboardWidgetSpec[];
  /** Event feeds composed by the shared calendar — Google Calendar plugs in here later (P3). */
  calendarSources?: CalendarSourceSpec[];
  /** Connect surfaces this integration contributes to the marketing picker (#411). */
  marketingConnectors?: MarketingConnectorSpec[];
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

/**
 * Is `name` an integration? (CLAUDE.md §6a)
 *
 * Falls back to `"module"` for a name this build has no web module for — an instance may mount an
 * API module whose web half this build does not ship, and answering "integration" for something
 * unknown would put it under a heading promising a credential nobody can produce.
 */
export function moduleKind(name: string): ModuleKind {
  return _modules.get(name)?.kind ?? "module";
}

/** A tenant's per-locale label for a nav entry / group (#169); `null`/absent = use the declared. */
export type NavLabelMap = Record<string, string> | null | undefined;

/** One saved sidebar entry (#169): a module nav key, optionally hidden, optionally renamed. */
export interface NavPrefItem {
  key: string;
  hidden?: boolean;
  /** The org's own label for this item (org-default row only); merged in by the API. */
  label?: NavLabelMap;
}

/**
 * Resolve a tenant nav label for the active locale, falling back to the other locale, then the
 * module's declared label (#169). Returns a *function* so it stays locale-reactive like the
 * declared `label()` it stands in for.
 */
export function resolveLabel(custom: NavLabelMap, fallback: () => string): () => string {
  if (!custom) return fallback;
  return () => {
    const loc = getLocale();
    const other = loc === "nl" ? "en" : "nl";
    return custom[loc]?.trim() || custom[other]?.trim() || fallback();
  };
}

export function navItemsFor(
  enabled: string[],
  user?: SessionUser | null,
  pref?: NavPrefItem[] | null,
): NavItem[] {
  // The org's custom labels ride on the pref (org default, merged by the API). Applied to every
  // returned item so the sidebar renders the tenant's own words, declared label as the fallback.
  const labelByKey = new Map((pref ?? []).map((item) => [item.key, item.label]));
  // The audience's own word first (a client reads "Bedrijven", never the agency's "Klanten"),
  // and the tenant's rename on top of either.
  const audienceLabel = (item: NavItem): (() => string) =>
    user?.isPortal && item.portalLabel ? item.portalLabel : item.label;
  const withLabel = (item: NavItem): NavItem =>
    labelByKey.has(item.key)
      ? { ...item, label: resolveLabel(labelByKey.get(item.key), audienceLabel(item)) }
      : { ...item, label: audienceLabel(item) };
  const base = enabledWebModules(enabled)
    .flatMap((m) => m.nav ?? [])
    .filter(
      (item) => !item.requiresPermission || can(user, item.requiresPermission, item.requiresScope),
    );
  if (!pref || pref.length === 0) {
    return base.sort((a, b) => (a.position ?? 100) - (b.position ?? 100)).map(withLabel);
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
    })
    .map(withLabel);
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

/**
 * The panels attached to `entityType` that **this viewer may read**, in display order — core's
 * plus the enabled modules'.
 *
 * `user` is required rather than optional on purpose: this is the function a host page's `load`
 * calls, and an optional argument is exactly how a new detail page would quietly ship the
 * ungated version. The client-side "which component draws this key" lookup is
 * {@link entityPanelComponent}, which needs no viewer — the server already decided.
 */
export function entityPanelsFor(
  enabled: string[],
  entityType: string,
  user: SessionUser | null | undefined,
): EntityPanelSpec[] {
  return (
    [..._coreEntityPanels, ...enabledWebModules(enabled).flatMap((m) => m.entityPanels ?? [])]
      .filter((p) => p.entityType === entityType)
      .filter((p) => !p.requiresPermission || can(user, p.requiresPermission, p.requiresScope))
      // The other axis (#274): a staff subject is not drawn for a client however many permissions
      // they hold. Filtered here rather than in each host page, so a detail page added tomorrow
      // inherits it — the same argument that put `requiresPermission` in this function.
      .filter((p) => p.audience !== "staff" || !user?.isPortal)
      .sort((a, b) => (a.position ?? 100) - (b.position ?? 100))
  );
}

/**
 * The registration behind one panel key — the browser-side half, mirroring
 * {@link companyPanelComponent}. No permission filter: the page renders the panels its `load`
 * returned, and those were already narrowed to the viewer.
 *
 * The whole spec rather than only its component, because a host now needs a second answer off
 * it: `prominence` (#404), which decides whether the panel is drawn as a working surface or as
 * a register. Two lookups over the same array would be two chances to disagree.
 */
export function entityPanelSpec(
  enabled: string[],
  entityType: string,
  key: string,
): EntityPanelSpec | undefined {
  return [
    ..._coreEntityPanels,
    ...enabledWebModules(enabled).flatMap((m) => m.entityPanels ?? []),
  ].find((p) => p.entityType === entityType && p.key === key);
}

export function entityPanelComponent(
  enabled: string[],
  entityType: string,
  key: string,
): EntityPanelSpec["component"] | undefined {
  return entityPanelSpec(enabled, entityType, key)?.component;
}

export function dashboardWidgetsFor(
  enabled: string[],
  user?: SessionUser | null,
): DashboardWidgetSpec[] {
  const audience = user?.isPortal ? "portal" : "staff";
  return enabledWebModules(enabled)
    .flatMap((m) => m.dashboardWidgets ?? [])
    .filter((w) => (w.audience ?? "staff") === audience)
    .filter((w) => !w.requiresPermission || can(user, w.requiresPermission))
    .sort((a, b) => (a.position ?? 100) - (b.position ?? 100));
}

export function calendarSourcesFor(enabled: string[]): CalendarSourceSpec[] {
  return enabledWebModules(enabled).flatMap((m) => m.calendarSources ?? []);
}

/**
 * The connect surfaces this tenant's enabled integrations contribute, filtered on the
 * permission each one's own POST declares (#310/#411).
 *
 * Filtered *before* mounting rather than inside the component: a control that can only ever
 * be refused should not be drawn (#253), and a contributed surface that has to remember its
 * own gate is #365's hope rather than #365's rule.
 */
export function marketingConnectorsFor(
  enabled: string[],
  user: SessionUser | null | undefined,
): MarketingConnectorSpec[] {
  return enabledWebModules(enabled)
    .flatMap((m) => m.marketingConnectors ?? [])
    .filter((c) => can(user, c.requiresPermission));
}
