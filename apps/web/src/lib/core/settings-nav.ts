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
import { hasPermission, type PermissionHolder } from "./permissions";

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

export const SETTINGS_GROUPS: readonly SettingsGroup[] = [
  { key: "personal", section: "personal", labelKey: null },
  { key: "workspace", section: "org", labelKey: "settings.group.workspace" },
  { key: "team_access", section: "org", labelKey: "settings.group.team_access" },
  { key: "data", section: "org", labelKey: "settings.group.data" },
  { key: "workflows", section: "org", labelKey: "settings.group.workflows" },
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
 * platform" from the two other third-party integrations. The five groups below each answer one
 * question: what does this workspace look like, who may use it, what shape is our data, how does
 * each module behave, and what does it talk to.
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
    key: "modules",
    href: "/settings/modules",
    titleKey: "settings.modules.title",
    subtitleKey: "settings.modules.subtitle",
    group: "workspace",
    permissions: ["settings.branding.write"],
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

  // --- Modules & werkprocessen ------------------------------------------- //
  {
    // A catalog staff touch day-to-day lives on the working page (#229); this is the deep link.
    key: "task-templates",
    href: "/tasks/templates",
    titleKey: "settings.task_templates.title",
    subtitleKey: "settings.task_templates.subtitle",
    keywordsKey: "settings.search.task_templates",
    group: "workflows",
    permissions: ["tasks.template.write", "tasks.checklist_template.write"],
    module: "tasks",
  },
  {
    key: "leave",
    href: "/settings/leave",
    titleKey: "settings.leave.title",
    subtitleKey: "settings.leave.subtitle",
    keywordsKey: "settings.search.leave",
    group: "workflows",
    permissions: ["leave.type.write"],
    module: "leave",
  },
  {
    key: "invoicing",
    href: "/settings/invoicing",
    titleKey: "settings.invoicing.title",
    subtitleKey: "settings.invoicing.subtitle",
    keywordsKey: "settings.search.invoicing",
    group: "workflows",
    permissions: ["invoicing.settings.manage"],
    module: "invoicing",
  },
  {
    key: "subscriptions",
    href: "/subscriptions/templates",
    titleKey: "settings.subscriptions.title",
    subtitleKey: "settings.subscriptions.subtitle",
    group: "workflows",
    permissions: ["subscriptions.template.manage"],
    module: "subscriptions",
  },
  {
    key: "domains",
    href: "/domains/tld-prices",
    titleKey: "settings.domains.title",
    subtitleKey: "settings.domains.subtitle",
    keywordsKey: "settings.search.domains",
    group: "workflows",
    permissions: ["domains.tld_price.read"],
    module: "domains",
  },
  {
    key: "providers",
    href: "/settings/providers",
    titleKey: "settings.providers.title",
    subtitleKey: "settings.providers.subtitle",
    keywordsKey: "settings.search.providers",
    group: "workflows",
    permissions: ["settings.providers.manage"],
  },
  {
    // Shared infrastructure (owner feedback): administered here, out of the main menu — the
    // client page shows websites, each naming its hosting.
    key: "hosting",
    href: "/settings/hosting",
    titleKey: "nav.hosting",
    subtitleKey: "settings.hosting.subtitle",
    group: "workflows",
    permissions: ["hosting.hosting.read"],
    module: "hosting",
  },
  {
    key: "automation",
    href: "/settings/automation",
    titleKey: "settings.automation.title",
    subtitleKey: "settings.automation.subtitle",
    keywordsKey: "settings.search.automation",
    group: "workflows",
    permissions: ["automation.rule.read"],
    module: "automation",
  },

  // --- Communicatie & koppelingen ---------------------------------------- //
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
    key: "notification-defaults",
    href: "/settings/notification-defaults",
    titleKey: "settings.notification_defaults.title",
    subtitleKey: "settings.notification_defaults.subtitle",
    keywordsKey: "settings.search.notification_defaults",
    group: "integrations",
    permissions: ["notifications.defaults.manage"],
    module: "notifications",
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
    key: "marketing",
    href: "/settings/marketing",
    titleKey: "settings.marketing.title",
    subtitleKey: "settings.marketing.subtitle",
    group: "integrations",
    permissions: ["marketing.link.manage"],
    module: "marketing",
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

/** Settings slug → its screen title key, for the breadcrumb resolver. */
export function settingsTitleKeys(): Record<string, string> {
  const out: Record<string, string> = {};
  for (const screen of SETTINGS_SCREENS) {
    const slug = screen.href.startsWith("/settings/") ? screen.href.slice("/settings/".length) : "";
    if (slug && !slug.includes("/")) out[slug] = screen.titleKey;
  }
  return out;
}
