<script lang="ts">
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";

  let { data } = $props();
</script>

<svelte:head>
  <title>{pageTitle(t("settings.title"))}</title>
</svelte:head>

{#snippet card(href: string, title: string, subtitle: string)}
  <a {href} class="rounded-xl border border-border bg-surface-raised p-5 hover:border-brand">
    <h3 class="text-sm font-semibold text-text">{title}</h3>
    <p class="mt-1 text-sm text-text-muted">{subtitle}</p>
  </a>
{/snippet}

<h1 class="mb-6 text-xl font-semibold text-text">{t("settings.title")}</h1>

<section class="mb-8">
  <h2 class="mb-3 text-xs font-semibold uppercase tracking-wide text-text-muted">
    {t("settings.section_personal")}
  </h2>
  <div class="grid gap-4 sm:grid-cols-2">
    {@render card("/settings/account", t("settings.account.title"), t("settings.account.subtitle"))}
    {@render card(
      "/settings/notifications",
      t("settings.notifications.title"),
      t("settings.notifications.subtitle"),
    )}
  </div>
</section>

<!-- The Org cards grouped into named sub-sections (#118) — a flat 13-card grid gave every
     setting equal weight and no scent for where anything lived. -->
<section>
  <h2 class="mb-3 text-xs font-semibold uppercase tracking-wide text-text-muted">
    {t("settings.section_org")}
  </h2>

  <h3 class="mb-2 text-sm font-medium text-text">{t("settings.group.team_access")}</h3>
  <div class="mb-6 grid gap-4 sm:grid-cols-2">
    {@render card("/settings/users", t("settings.users.title"), t("settings.users.subtitle"))}
    {@render card("/settings/roles", t("settings.roles.title"), t("settings.roles.subtitle"))}
    {@render card(
      "/settings/company-groups",
      t("settings.company_groups.title"),
      t("settings.company_groups.subtitle"),
    )}
    {@render card(
      "/settings/service-accounts",
      t("settings.service_accounts.title"),
      t("settings.service_accounts.subtitle"),
    )}
    {@render card("/settings/sso", t("settings.sso.title"), t("settings.sso.subtitle"))}
  </div>

  <h3 class="mb-2 text-sm font-medium text-text">{t("settings.group.brand_platform")}</h3>
  <div class="mb-6 grid gap-4 sm:grid-cols-2">
    {@render card(
      "/settings/branding",
      t("settings.branding.title"),
      t("settings.branding.subtitle"),
    )}
    {@render card("/settings/modules", t("settings.modules.title"), t("settings.modules.subtitle"))}
    {@render card("/settings/google", t("settings.google.title"), t("settings.google.subtitle"))}
  </div>

  <h3 class="mb-2 text-sm font-medium text-text">{t("settings.group.customization")}</h3>
  <div class="mb-6 grid gap-4 sm:grid-cols-2">
    {@render card(
      "/settings/custom-fields",
      t("settings.custom_fields.title"),
      t("settings.custom_fields.subtitle"),
    )}
    {@render card(
      "/settings/contact-types",
      t("settings.contact_types.title"),
      t("settings.contact_types.subtitle"),
    )}
    {@render card(
      "/settings/interaction-kinds",
      t("settings.interaction_kinds.title"),
      t("settings.interaction_kinds.subtitle"),
    )}
    {@render card(
      "/settings/time-entry-types",
      t("settings.time_entry_types.title"),
      t("settings.time_entry_types.subtitle"),
    )}
    {@render card(
      "/settings/providers",
      t("settings.providers.title"),
      t("settings.providers.subtitle"),
    )}
    <!-- Hosting is shared infrastructure (owner feedback): administered here, out of the
         main menu — the client page shows websites, each naming its hosting. -->
    {@render card("/settings/hosting", t("nav.hosting"), t("settings.hosting.subtitle"))}
    {@render card("/settings/impex", t("impex.settings.title"), t("impex.settings.subtitle"))}
  </div>

  <h3 class="mb-2 text-sm font-medium text-text">{t("settings.group.workflows")}</h3>
  <div class="grid gap-4 sm:grid-cols-2">
    {@render card(
      "/tasks/templates",
      t("settings.task_templates.title"),
      t("settings.task_templates.subtitle"),
    )}
    {@render card(
      "/settings/task-labels",
      t("settings.task_labels.title"),
      t("settings.task_labels.subtitle"),
    )}
    {@render card(
      "/settings/task-statuses",
      t("settings.task_statuses.title"),
      t("settings.task_statuses.subtitle"),
    )}
    {@render card("/settings/leave", t("settings.leave.title"), t("settings.leave.subtitle"))}
    {@render card(
      "/settings/subscriptions",
      t("settings.subscriptions.title"),
      t("settings.subscriptions.subtitle"),
    )}
    {@render card(
      "/settings/invoicing",
      t("settings.invoicing.title"),
      t("settings.invoicing.subtitle"),
    )}
    {@render card(
      "/settings/dashboard",
      t("settings.dashboard.title"),
      t("settings.dashboard.subtitle"),
    )}
    {@render card(
      "/settings/navigation",
      t("settings.navigation.title"),
      t("settings.navigation.subtitle"),
    )}
    {@render card(
      "/settings/notification-defaults",
      t("settings.notification_defaults.title"),
      t("settings.notification_defaults.subtitle"),
    )}
    {@render card("/settings/email", t("settings.email.title"), t("settings.email.subtitle"))}
    <!-- Cloud only (epic #199): the tenant's switch on platform-support access. -->
    {#if data.cloud}
      {@render card(
        "/settings/service-access",
        t("settings.service_access.title"),
        t("settings.service_access.card_subtitle"),
      )}
    {/if}
    {@render card(
      "/settings/automation",
      t("settings.automation.title"),
      t("settings.automation.subtitle"),
    )}
    {@render card(
      "/settings/marketing",
      t("settings.marketing.title"),
      t("settings.marketing.subtitle"),
    )}
    {@render card("/settings/ai", t("settings.ai.title"), t("settings.ai.subtitle"))}
  </div>
</section>

<!--
  A third scope. Personal settings belong to the user and org settings to the tenant; version,
  health and migrations belong to the *installation* — they survive every org on the box and
  would not be an org admin's business in a multi-org deploy (CLAUDE.md §5).
-->
<section class="mt-8">
  <h2 class="mb-3 text-xs font-semibold uppercase tracking-wide text-text-muted">
    {t("settings.section_system")}
  </h2>
  <div class="grid gap-4 sm:grid-cols-2">
    {@render card("/settings/system", t("settings.system.title"), t("settings.system.subtitle"))}
    <!-- Instance-owner only (issue #137): the license belongs to the installation, so it
         lives in the installation section — not among the org's own knobs. Modules stays
         org-side on purpose: enabled_modules is org_settings data, an org admin's business. -->
    {#if data.user?.isInstanceOwner}
      {@render card(
        "/settings/license",
        t("settings.license.title"),
        t("settings.license.subtitle"),
      )}
    {/if}
  </div>
</section>
