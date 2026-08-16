/**
 * The Instellingen registry — every settings screen, described once.
 *
 * Instellingen had grown to 35 screens described in four places at once: the index page's card
 * grid, `SETTINGS_SCREEN_PERMISSIONS` (which decides whether the sidebar link shows at all), the
 * breadcrumb resolver's slug→title map, and each screen's own `+page.server.ts` guard. They had
 * drifted: the permission list named `domains.tld_price.manage` and `subscriptions.type.manage`
 * for screens that actually guard on `domains.tld_price.read` and `subscriptions.template.manage`,
 * and it knew nothing about eight screens at all — so an admin holding only `settings.nav.manage`
 * could not reach Instellingen even though a screen there was theirs.
 *
 * Worse, the index rendered **every** card to anyone who could open **any** screen. An agency that
 * hands someone `settings.branding.write` and nothing else showed them thirty cards, twenty-nine of
 * which bounced back to the dashboard on click — exactly the failure docs/UX.md records as "a
 * control that renders without checking `can()`" (#253).
 *
 * So: one list. The index grid, the section rail, the breadcrumb labels and `canAccessSettings` all
 * read it, and a new screen becomes visible everywhere by being added here once.
 *
 * Visibility has three independent axes, all of which are **UX, not security** — every screen still
 * guards itself server-side (CLAUDE.md §15):
 *  - `permissions` — holding *any* one of them opens the screen (a couple of screens are two
 *    repositories behind one route). No list means every member may open it.
 *  - `module` — a module the tenant has switched off has no screen to configure. Without this the
 *    owner (`*`) saw a Verlof card whose API routes are not even mounted.
 *  - `cloudOnly` / `instanceOwnerOnly` — posture, not permission: `settings.service_access.manage`
 *    is satisfied by the owner's wildcard on a self-hosted box where the capability does not exist.
 */
import { hasPermission, type PermissionHolder } from "./permissions.ts";

export type SettingsSectionKey = "personal" | "org" | "system";

export interface SettingsSection {
  key: SettingsSectionKey;
  labelKey: string;
}

/** The three scopes a setting can belong to: me, the tenant, the installation (CLAUDE.md §5). */
export const SETTINGS_SECTIONS: readonly SettingsSection[] = [
  { key: "personal", labelKey: "settings.section_personal" },
  { key: "org", labelKey: "settings.section_org" },
  { key: "system", labelKey: "settings.section_system" },
];

export interface SettingsGroup {
  key: string;
  section: SettingsSectionKey;
  /** `null` where the section heading already says it (Mijn instellingen, Systeem). */
  labelKey: string | null;
}

/**
 * The `modules` / `integrations` pair is the one that carries a rule rather than a preference
 * (CLAUDE.md §6a). A screen owned by an **integration** — a module whose whole job is holding a
 * credential for somebody else's service — belongs in `integrations`; a screen owned by a plain
 * module belongs in `modules`. `settings-nav.test.ts` asserts exactly that against the web
 * registry's `moduleKind`, because the old single "Communicatie & koppelingen" group had drifted
 * into holding Marketing, Rapportage and Meldingen (three capabilities of ours) beside Google,
 * Cloudflare and Mollie (three accounts somebody else owns) — and a reader looking for "what do
 * we connect to" had to know the product to tell which was which.
 *
 * A screen with no `module` may sit in either: E-mail and AI are core seams that still talk to a
 * third party, and SSO stays under Team & toegang because what it configures is who may sign in,
 * not what we read from someone.
 */
export const SETTINGS_GROUPS: readonly SettingsGroup[] = [
  { key: "personal", section: "personal", labelKey: null },
  { key: "workspace", section: "org", labelKey: "settings.group.workspace" },
  { key: "team_access", section: "org", labelKey: "settings.group.team_access" },
  { key: "data", section: "org", labelKey: "settings.group.data" },
  { key: "modules", section: "org", labelKey: "settings.group.modules" },
  { key: "integrations", section: "org", labelKey: "settings.group.integrations" },
  { key: "system", section: "system", labelKey: null },
];

export interface SettingsScreen {
  /** Stable slug; the last path segment for anything under `/settings/`. */
  key: string;
  href: string;
  titleKey: string;
  subtitleKey: string;
  /**
   * Extra search terms, never rendered. The index search matches title + subtitle already; this
   * carries what the card text cannot say — "wachtwoord" and "2fa" live on Mijn account, "btw" and
   * "kvk" on Facturatie — so the answer to "where do I change X" is one keystroke, not a hunt
   * through five groups.
   */
  keywordsKey?: string;
  group: string;
  /** Holding **any** of these opens it. Absent = every member (personal screens). */
  permissions?: readonly string[];
  /** Owned by a module: gone when the tenant switched it off. */
  module?: string;
  cloudOnly?: boolean;
  instanceOwnerOnly?: boolean;
}

/**
 * Ordered within each group. The order is the reading order on both the index and the rail.
 *
 * The grouping is the audit's other half. "Modules & workflows" had become a fifteen-card junk
 * drawer holding the org's dashboard defaults, its outgoing mail transport, its AI provider and a
 * cloud support switch side by side, while Google Workspace sat two groups away under "Merk &
 * platform" from the two other third-party integrations. The five groups that replaced it each
 * answered one question: what does this workspace look like, who may use it, what shape is our
 * data, how does each module behave, and what does it talk to.
 *
 * The last of those five was still answering two. "Communicatie & koppelingen" held Marketing,
 * Rapportage and Meldingen — three things schakl does — beside Google, Cloudflare, Uptime Kuma,
 * OXXA and Mollie, which are five accounts belonging to somebody else, each with a credential
 * that can expire and a vendor that can change its mind. Those are not the same kind of setting
 * and they do not fail the same way: a module is configured, an integration is *connected*. So
 * they are two groups now (CLAUDE.md §6a), and which one a screen lands in is derived from the
 * module that owns it rather than decided per screen.
 */
export const SETTINGS_SCREENS: readonly SettingsScreen[] = [
  // --- Mijn instellingen ------------------------------------------------- //
  {
    key: "account",
    href: "/settings/account",
    titleKey: "settings.account.title",
    subtitleKey: "settings.account.subtitle",
    keywordsKey: "settings.search.account",
    group: "personal",
  },
  {
    // Personal API keys + the MCP connection guide. Its own screen rather than the card it was
    // on Mijn account: a staged flow with a one-shot secret reveal in the middle is not a field
    // you fill in and save, and the MCP surface had no home in the product at all.
    key: "api",
    href: "/settings/api",
    titleKey: "settings.api.title",
    subtitleKey: "settings.api.subtitle",
    keywordsKey: "settings.search.api",
    group: "personal",
    permissions: ["apikeys.personal.manage"],
  },
  {
    key: "notifications",
    href: "/settings/notifications",
    titleKey: "settings.notifications.title",
    subtitleKey: "settings.notifications.subtitle",
    keywordsKey: "settings.search.notifications",
    group: "personal",
    module: "notifications",
  },

  // --- Werkruimte -------------------------------------------------------- //
  {
    key: "branding",
    href: "/settings/branding",
    titleKey: "settings.branding.title",
    subtitleKey: "settings.branding.subtitle",
    keywordsKey: "settings.search.branding",
    group: "workspace",
    permissions: ["settings.branding.write"],
  },
  {
    // The custom-domain wizard (#292). Its own screen rather than a block on Huisstijl: it is
    // a staged, resumable flow with its own polling, not a field you fill in and save.
    key: "domain",
    href: "/settings/domain",
    titleKey: "settings.domain.title",
    subtitleKey: "settings.domain.subtitle",
    keywordsKey: "settings.search.domain",
    group: "workspace",
    permissions: ["settings.domain.write"],
  },
  {
    key: "navigation",
    href: "/settings/navigation",
    titleKey: "settings.navigation.title",
    subtitleKey: "settings.navigation.subtitle",
    keywordsKey: "settings.search.navigation",
    group: "workspace",
    permissions: ["settings.nav.manage"],
  },
  {
    key: "dashboard",
    href: "/settings/dashboard",
    titleKey: "settings.dashboard.title",
    subtitleKey: "settings.dashboard.subtitle",
    keywordsKey: "settings.search.dashboard",
    group: "workspace",
    permissions: ["settings.dashboard.manage"],
  },

  // --- Team & toegang ---------------------------------------------------- //
  {
    key: "users",
    href: "/settings/users",
    titleKey: "settings.users.title",
    subtitleKey: "settings.users.subtitle",
    keywordsKey: "settings.search.users",
    group: "team_access",
    permissions: ["members.member.read"],
  },
  {
    key: "roles",
    href: "/settings/roles",
    titleKey: "settings.roles.title",
    subtitleKey: "settings.roles.subtitle",
    keywordsKey: "settings.search.roles",
    group: "team_access",
    permissions: ["settings.roles.manage"],
  },
  {
    key: "company-groups",
    href: "/settings/company-groups",
    titleKey: "settings.company_groups.title",
    subtitleKey: "settings.company_groups.subtitle",
    group: "team_access",
    permissions: ["companies.group.manage"],
  },
  {
    key: "sso",
    href: "/settings/sso",
    titleKey: "settings.sso.title",
    subtitleKey: "settings.sso.subtitle",
    keywordsKey: "settings.search.sso",
    group: "team_access",
    permissions: ["settings.auth.manage"],
  },
  {
    key: "service-accounts",
    href: "/settings/service-accounts",
    titleKey: "settings.service_accounts.title",
    subtitleKey: "settings.service_accounts.subtitle",
    keywordsKey: "settings.search.service_accounts",
    group: "team_access",
    permissions: ["apikeys.service_account.manage"],
  },
  {
    // Access, not workflow: it decides who may open this org's data (epic #199).
    key: "service-access",
    href: "/settings/service-access",
    titleKey: "settings.service_access.title",
    subtitleKey: "settings.service_access.card_subtitle",
    group: "team_access",
    permissions: ["settings.service_access.manage"],
    cloudOnly: true,
  },

  // --- Gegevens & keuzelijsten ------------------------------------------- //
  {
    key: "custom-fields",
    href: "/settings/custom-fields",
    titleKey: "settings.custom_fields.title",
    subtitleKey: "settings.custom_fields.subtitle",
    keywordsKey: "settings.search.custom_fields",
    group: "data",
    permissions: ["settings.customfields.write"],
  },
  {
    key: "companies",
    href: "/settings/companies",
    titleKey: "settings.companies.title",
    subtitleKey: "settings.companies.subtitle",
    keywordsKey: "settings.search.companies",
    group: "data",
    permissions: ["companies.settings.manage"],
  },
  {
    key: "contact-types",
    href: "/settings/contact-types",
    titleKey: "settings.contact_types.title",
    subtitleKey: "settings.contact_types.subtitle",
    group: "data",
    permissions: ["contacts.type.manage"],
    module: "contacts",
  },
  {
    key: "interaction-kinds",
    href: "/settings/interaction-kinds",
    titleKey: "settings.interaction_kinds.title",
    subtitleKey: "settings.interaction_kinds.subtitle",
    group: "data",
    permissions: ["interactions.kind.manage"],
    module: "interactions",
  },
  {
    key: "time-entry-types",
    href: "/settings/time-entry-types",
    titleKey: "settings.time_entry_types.title",
    subtitleKey: "settings.time_entry_types.subtitle",
    group: "data",
    permissions: ["time.entry_type.manage"],
    module: "time",
  },
  {
    key: "task-labels",
    href: "/settings/task-labels",
    titleKey: "settings.task_labels.title",
    subtitleKey: "settings.task_labels.subtitle",
    group: "data",
    permissions: ["tasks.label.write"],
    module: "tasks",
  },
  {
    key: "task-statuses",
    href: "/settings/task-statuses",
    titleKey: "settings.task_statuses.title",
    subtitleKey: "settings.task_statuses.subtitle",
    keywordsKey: "settings.search.task_statuses",
    group: "data",
    permissions: ["tasks.status.write"],
    module: "tasks",
  },
  {
    key: "impex",
    href: "/settings/impex",
    titleKey: "impex.settings.title",
    subtitleKey: "impex.settings.subtitle",
    keywordsKey: "settings.search.impex",
    group: "data",
    permissions: ["impex.export"],
  },

  // --- Modules ----------------------------------------------------------- //
  {
    // **First in its own group, and that placement is the point** (issue #378). This screen used
    // to sit in Werkruimte, third card down, while fourteen cards further along a *group heading*
    // also read "Modules" — so the word named two different things on one page, and the reader
    // asking "where do I switch Facturatie on" had to already know which of the two was meant.
    // The switch now heads the list of the things it switches, which is the only arrangement where
    // the heading and the screen can never disagree.
    key: "modules",
    href: "/settings/modules",
    titleKey: "settings.modules.title",
    subtitleKey: "settings.modules.subtitle",
    keywordsKey: "settings.search.modules",
    group: "modules",
    permissions: ["settings.branding.write"],
  },
  {
    // A catalog staff touch day-to-day lives on the working page (#229); this is the deep link.
    key: "task-templates",
    href: "/tasks/templates",
    titleKey: "settings.task_templates.title",
    subtitleKey: "settings.task_templates.subtitle",
    keywordsKey: "settings.search.task_templates",
    group: "modules",
    permissions: ["tasks.template.write", "tasks.checklist_template.write"],
    module: "tasks",
  },
  {
    key: "leave",
    href: "/settings/leave",
    titleKey: "settings.leave.title",
    subtitleKey: "settings.leave.subtitle",
    keywordsKey: "settings.search.leave",
    group: "modules",
    permissions: ["leave.type.write"],
    module: "leave",
  },
  {
    key: "invoicing",
    href: "/settings/invoicing",
    titleKey: "settings.invoicing.title",
    subtitleKey: "settings.invoicing.subtitle",
    keywordsKey: "settings.search.invoicing",
    group: "modules",
    permissions: ["invoicing.settings.manage"],
    module: "invoicing",
  },
  {
    key: "subscriptions",
    href: "/subscriptions/templates",
    titleKey: "settings.subscriptions.title",
    subtitleKey: "settings.subscriptions.subtitle",
    group: "modules",
    permissions: ["subscriptions.template.manage"],
    module: "subscriptions",
  },
  {
    key: "domains",
    href: "/domains/tld-prices",
    titleKey: "settings.domains.title",
    subtitleKey: "settings.domains.subtitle",
    keywordsKey: "settings.search.domains",
    group: "modules",
    permissions: ["domains.tld_price.read"],
    module: "domains",
  },
  {
    key: "providers",
    href: "/settings/providers",
    titleKey: "settings.providers.title",
    subtitleKey: "settings.providers.subtitle",
    keywordsKey: "settings.search.providers",
    group: "modules",
    permissions: ["settings.providers.manage"],
  },
  {
    // Shared infrastructure (owner feedback): administered here, out of the main menu — the
    // client page shows websites, each naming its hosting.
    key: "hosting",
    href: "/settings/hosting",
    titleKey: "nav.hosting",
    subtitleKey: "settings.hosting.subtitle",
    group: "modules",
    permissions: ["hosting.hosting.read"],
    module: "hosting",
  },
  {
    key: "automation",
    href: "/settings/automation",
    titleKey: "settings.automation.title",
    subtitleKey: "settings.automation.subtitle",
    keywordsKey: "settings.search.automation",
    group: "modules",
    permissions: ["automation.rule.read"],
    module: "automation",
  },

  {
    key: "notification-defaults",
    href: "/settings/notification-defaults",
    titleKey: "settings.notification_defaults.title",
    subtitleKey: "settings.notification_defaults.subtitle",
    keywordsKey: "settings.search.notification_defaults",
    group: "modules",
    permissions: ["notifications.defaults.manage"],
    module: "notifications",
  },
  {
    key: "marketing",
    href: "/settings/marketing",
    titleKey: "settings.marketing.title",
    subtitleKey: "settings.marketing.subtitle",
    keywordsKey: "settings.search.marketing",
    group: "modules",
    permissions: ["marketing.link.manage"],
    module: "marketing",
  },
  {
    // The house voice, the document templates and the org-wide schedule (#300). A client's own
    // profile is *not* here — it belongs on the client, beside everything else about them.
    key: "reporting",
    href: "/settings/reporting",
    titleKey: "settings.reporting.title",
    subtitleKey: "settings.reporting.subtitle",
    group: "modules",
    permissions: ["reporting.settings.manage"],
    module: "reporting",
  },

  // --- Integraties ------------------------------------------------------- //
  {
    // The other half of the split (#378), and the reason it is a second screen rather than a
    // second section of one: a module and an integration are answered by different people at
    // different moments and fail differently. A module is *configured* and works; an integration
    // is *connected* and stops working the day somebody else revokes a token. One form with one
    // Save said neither, and put the switch for the whole Integraties group on a page called
    // Modules — so the eight screens that need a credential had no way in from the group that
    // holds them.
    key: "integrations",
    href: "/settings/integrations",
    titleKey: "settings.integrations.title",
    subtitleKey: "settings.integrations.subtitle",
    keywordsKey: "settings.search.integrations",
    group: "integrations",
    permissions: ["settings.branding.write"],
  },
  {
    key: "email",
    href: "/settings/email",
    titleKey: "settings.email.title",
    subtitleKey: "settings.email.subtitle",
    keywordsKey: "settings.search.email",
    group: "integrations",
    permissions: ["settings.email.manage"],
  },
  {
    key: "google",
    href: "/settings/google",
    titleKey: "settings.google.title",
    subtitleKey: "settings.google.subtitle",
    keywordsKey: "settings.search.google",
    group: "integrations",
    permissions: ["google.settings.manage"],
    module: "google",
  },
  {
    key: "ai",
    href: "/settings/ai",
    titleKey: "settings.ai.title",
    subtitleKey: "settings.ai.subtitle",
    keywordsKey: "settings.search.ai",
    group: "integrations",
    permissions: ["ai.settings.manage"],
  },
  {
    // Its own card rather than a section of Marketing: the developer token identifies the
    // *agency* to Google, the account links decide whose money is being spent, and the write
    // switch stops every mutating call at once. All three are decisions an owner makes, and
    // none of them belongs behind a dashboard's settings.
    key: "google_ads",
    href: "/settings/google-ads",
    titleKey: "settings.google_ads.title",
    subtitleKey: "settings.google_ads.subtitle",
    group: "integrations",
    permissions: ["google_ads.settings.manage"],
    module: "google_ads",
  },
  {
    // Uptime Kuma lives here rather than on a website, for principle 6's reason: it holds the
    // credential, and the credential is org-wide configuration. `uptime.monitor.read` does not
    // open it — seeing that a client's site is down and holding the administrator account of
    // the box that watches it are different capabilities.
    key: "uptime",
    href: "/settings/uptime",
    titleKey: "settings.uptime.title",
    subtitleKey: "settings.uptime.subtitle",
    keywordsKey: "settings.search.uptime",
    group: "integrations",
    permissions: ["uptime.instance.manage"],
    module: "uptime",
  },
  {
    key: "cloudflare",
    href: "/settings/cloudflare",
    titleKey: "settings.cloudflare.title",
    subtitleKey: "settings.cloudflare.subtitle",
    keywordsKey: "settings.search.cloudflare",
    group: "integrations",
    permissions: ["cloudflare.settings.manage"],
    module: "cloudflare",
  },
  {
    // The registrar half of the same story (#296): the reseller logins, and the register they
    // pull. `oxxa.registrar.sync` deliberately does *not* open it — that permission acts
    // *through* a credential, this screen holds one.
    key: "oxxa",
    href: "/settings/oxxa",
    // The card's own copy, like every other entry here — the screen's `<h1>` and lead sentence
    // are a different, longer register (`oxxa.settings.title` / `.description`). Without the
    // keywords the screen could not be found by searching "registrar" or "nameservers", which is
    // exactly what this list exists to prevent.
    titleKey: "settings.oxxa.title",
    subtitleKey: "settings.oxxa.subtitle",
    keywordsKey: "settings.search.oxxa",
    group: "integrations",
    permissions: ["oxxa.settings.manage"],
    module: "oxxa",
  },
  {
    // The payment half (epic #269): the Mollie keys an invoice is collected through. It sits
    // with the other integrations rather than under Facturatie because what is configured here
    // is a credential and a conversation with somebody else's service — `invoicing` owns what a
    // payment *means*, and never learns which provider took it (`app/core/payments`).
    //
    // `invoicing.payment.link` deliberately does **not** open it: that permission spends a
    // credential (it starts a checkout, and a client's own portal login holds it at `:own`),
    // while this screen holds one. Same split as OXXA's sync-versus-settings.
    key: "mollie",
    href: "/settings/mollie",
    titleKey: "settings.mollie.title",
    subtitleKey: "settings.mollie.subtitle",
    // Without these, the screen could not be found by searching "ideal", "creditcard" or
    // "webhook" — the words somebody actually types when a payment did not come through, and
    // none of which the card's own title and subtitle contain.
    keywordsKey: "settings.search.mollie",
    group: "integrations",
    permissions: ["mollie.settings.manage"],
    module: "mollie",
  },
  {
    // The accounting half (epic #377, issue #31): the SnelStart administration an agency's
    // invoices, clients and articles travel to, and the outstanding balances that come back.
    // An integration, not a section of Facturatie — `invoicing` owns what an invoice *is* and
    // never learns which accounting package it was booked in (`invoicing.accounting`).
    //
    // **Two permissions, deliberately**, and this is the only integration card that names two.
    // `snelstart.settings.manage` holds the credential; `snelstart.sync.run` acts through it —
    // pulling the chart of accounts, pairing relations, reconciling who has paid. The second is
    // an ordinary bookkeeping job an agency hands to somebody who has no business rotating a
    // koppelsleutel, and the screen is where that job is done, so hiding the card from them
    // would be hiding the work. The load mirrors the split rather than restating it: a
    // sync-only holder reads the administrations through `/accounts/options` (which declares
    // `sync.run`) and sees no credential control at all.
    key: "snelstart",
    href: "/settings/snelstart",
    titleKey: "settings.snelstart.title",
    subtitleKey: "settings.snelstart.subtitle",
    // Without these the screen could not be found by searching "boekhouding", "grootboek" or
    // "koppelsleutel" — the words somebody types when an invoice did not reach the accountant,
    // and none of which the card's own title and subtitle contain.
    keywordsKey: "settings.search.snelstart",
    group: "integrations",
    permissions: ["snelstart.settings.manage", "snelstart.sync.run"],
    module: "snelstart",
  },

  // --- Systeem ----------------------------------------------------------- //
  {
    key: "system",
    href: "/settings/system",
    titleKey: "settings.system.title",
    subtitleKey: "settings.system.subtitle",
    keywordsKey: "settings.search.system",
    group: "system",
    permissions: ["settings.system.read"],
  },
  {
    // The license belongs to the installation, not to a tenant (issue #137).
    key: "license",
    href: "/settings/license",
    titleKey: "settings.license.title",
    subtitleKey: "settings.license.subtitle",
    group: "system",
    instanceOwnerOnly: true,
  },
];

/** What deciding visibility needs, and nothing more — so a server load can pass `locals`. */
export interface SettingsViewer {
  user: (PermissionHolder & { isInstanceOwner?: boolean }) | null | undefined;
  enabledModules: readonly string[] | undefined;
  cloud: boolean;
}

export function screenVisible(screen: SettingsScreen, viewer: SettingsViewer): boolean {
  if (screen.cloudOnly && !viewer.cloud) return false;
  if (screen.instanceOwnerOnly) return viewer.user?.isInstanceOwner === true;
  if (screen.module && !(viewer.enabledModules ?? []).includes(screen.module)) return false;
  if (!screen.permissions?.length) return true;
  return screen.permissions.some((key) => hasPermission(viewer.user?.permissions, key));
}

export function visibleSettingsScreens(viewer: SettingsViewer): SettingsScreen[] {
  return SETTINGS_SCREENS.filter((screen) => screenVisible(screen, viewer));
}

/** Resolves an i18n key. Passed in rather than imported so this module stays testable and stays
 *  usable from a server load, which asks it for `canAccessSettings` and translates nothing. */
export type Translate = (key: string) => string;

/**
 * Everything a search may match on one screen: its title, its subtitle, and the hidden keywords
 * that carry what the card text cannot say ("btw" finds Facturatie, "wachtwoord" finds Mijn
 * account, "ideal" finds Mollie).
 */
export function settingsHaystack(screen: SettingsScreen, translate: Translate): string {
  const extra = screen.keywordsKey ? ` ${translate(screen.keywordsKey)}` : "";
  return `${translate(screen.titleKey)} ${translate(screen.subtitleKey)}${extra}`.toLowerCase();
}

/**
 * The screens matching every word of `query`. Every word, not any: with 38 screens an OR search
 * for "google ads" returns most of the integrations group, which is not a search result.
 */
export function matchSettingsScreens(
  screens: readonly SettingsScreen[],
  query: string,
  translate: Translate,
): SettingsScreen[] {
  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (terms.length === 0) return [...screens];
  return screens.filter((screen) => {
    const hay = settingsHaystack(screen, translate);
    return terms.every((word) => hay.includes(word));
  });
}

export interface SettingsGroupView extends SettingsGroup {
  items: SettingsScreen[];
}

export interface SettingsSectionView extends SettingsSection {
  groups: SettingsGroupView[];
}

/**
 * `screens` folded into the section → group → item tree both the index grid and the rail render,
 * with empty groups and empty sections dropped.
 *
 * Shared rather than written twice because the two had already been written twice and were about
 * to be written a third time when the rail grew its own search: the index filtered by query and
 * the rail did not, so typing "mollie" on the index narrowed the page to one card while the rail
 * beside it still listed all thirty-eight. One function, one answer, and the search box can be
 * put anywhere.
 */
export function groupSettingsScreens(screens: readonly SettingsScreen[]): SettingsSectionView[] {
  return SETTINGS_SECTIONS.map((section) => ({
    ...section,
    groups: SETTINGS_GROUPS.filter((group) => group.section === section.key)
      .map((group) => ({ ...group, items: screens.filter((s) => s.group === group.key) }))
      .filter((group) => group.items.length > 0),
  })).filter((section) => section.groups.length > 0);
}

/**
 * Instellingen is reachable by anyone who can open at least one **org** screen inside it — an
 * agency may hand someone `settings.branding.write` and nothing else, and Instellingen must then
 * still be findable. Derived from the list above so it can never again name a permission no screen
 * uses, or miss a screen someone holds.
 *
 * The personal screens are deliberately excluded: every member holds those, and counting them would
 * put the manager-only Instellingen item (docs/UX.md, Navigation) in everyone's sidebar. A member
 * reaches Mijn account through the profile menu, which is where personal settings belong.
 *
 * Deliberately permissive on the other two axes: `enabledModules` and the cloud posture are not
 * always to hand where the sidebar decides, and the index handles an empty result honestly.
 */
export function canAccessSettings(granted: readonly string[] | undefined): boolean {
  return SETTINGS_SCREENS.some(
    (screen) =>
      screen.group !== "personal" &&
      !screen.instanceOwnerOnly &&
      screen.permissions?.some((key) => hasPermission(granted, key)),
  );
}

/**
 * The one settings screen that configures `module`, or `null` — for the enable/disable screens,
 * which owe the reader a way onward (issue #378).
 *
 * Switching a module on used to end the sentence: the screen never said that an integration does
 * nothing until a credential is entered, nor where that is done, and the rail it would have to be
 * found in did not show it (§1 of #378). A link on the row closes that.
 *
 * **Exactly one, or none.** `tasks` owns three settings screens (labels, statuses, templates) and
 * picking one of them would be picking arbitrarily on the reader's behalf; `wordpress` owns none,
 * because its credential lives on a website. Both answer `null` and render no link, which is the
 * honest outcome — a link that lands on one of three is worse than the reader opening the group.
 */
export function settingsScreenForModule(module: string): SettingsScreen | null {
  const owned = SETTINGS_SCREENS.filter((screen) => screen.module === module);
  return owned.length === 1 ? owned[0] : null;
}

/** Settings slug → its screen title key, for the breadcrumb resolver. */
export function settingsTitleKeys(): Record<string, string> {
  const out: Record<string, string> = {};
  for (const screen of SETTINGS_SCREENS) {
    const slug = screen.href.startsWith("/settings/") ? screen.href.slice("/settings/".length) : "";
    if (slug && !slug.includes("/")) out[slug] = screen.titleKey;
  }
  return out;
}
