<script lang="ts">
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
</script>

<svelte:head>
  <title>{pageTitle(t("settings.title"))}</title>
</svelte:head>

<h1 class="mb-6 text-xl font-semibold text-text">{t("settings.title")}</h1>

<section class="mb-8">
  <h2 class="mb-3 text-xs font-semibold uppercase tracking-wide text-text-muted">
    {t("settings.section_personal")}
  </h2>
  <div class="grid gap-4 sm:grid-cols-2">
    <a
      href="/settings/account"
      class="rounded-xl border border-border bg-surface-raised p-5 hover:border-brand"
    >
      <h3 class="text-sm font-semibold text-text">{t("settings.account.title")}</h3>
      <p class="mt-1 text-sm text-text-muted">{t("settings.account.subtitle")}</p>
    </a>
    <a
      href="/settings/notifications"
      class="rounded-xl border border-border bg-surface-raised p-5 hover:border-brand"
    >
      <h3 class="text-sm font-semibold text-text">{t("settings.notifications.title")}</h3>
      <p class="mt-1 text-sm text-text-muted">{t("settings.notifications.subtitle")}</p>
    </a>
  </div>
</section>

<section>
  <h2 class="mb-3 text-xs font-semibold uppercase tracking-wide text-text-muted">
    {t("settings.section_org")}
  </h2>
  <div class="grid gap-4 sm:grid-cols-2">
    <a
      href="/settings/users"
      class="rounded-xl border border-border bg-surface-raised p-5 hover:border-brand"
    >
      <h3 class="text-sm font-semibold text-text">{t("settings.users.title")}</h3>
      <p class="mt-1 text-sm text-text-muted">{t("settings.users.subtitle")}</p>
    </a>
    <a
      href="/settings/roles"
      class="rounded-xl border border-border bg-surface-raised p-5 hover:border-brand"
    >
      <h3 class="text-sm font-semibold text-text">{t("settings.roles.title")}</h3>
      <p class="mt-1 text-sm text-text-muted">{t("settings.roles.subtitle")}</p>
    </a>
    <a
      href="/settings/custom-fields"
      class="rounded-xl border border-border bg-surface-raised p-5 hover:border-brand"
    >
      <h3 class="text-sm font-semibold text-text">{t("settings.custom_fields.title")}</h3>
      <p class="mt-1 text-sm text-text-muted">{t("settings.custom_fields.subtitle")}</p>
    </a>
    <a
      href="/settings/providers"
      class="rounded-xl border border-border bg-surface-raised p-5 hover:border-brand"
    >
      <h3 class="text-sm font-semibold text-text">{t("settings.providers.title")}</h3>
      <p class="mt-1 text-sm text-text-muted">{t("settings.providers.subtitle")}</p>
    </a>
    <a
      href="/settings/email"
      class="rounded-xl border border-border bg-surface-raised p-5 hover:border-brand"
    >
      <h3 class="text-sm font-semibold text-text">{t("settings.email.title")}</h3>
      <p class="mt-1 text-sm text-text-muted">{t("settings.email.subtitle")}</p>
    </a>
    <a
      href="/settings/contact-types"
      class="rounded-xl border border-border bg-surface-raised p-5 hover:border-brand"
    >
      <h3 class="text-sm font-semibold text-text">{t("settings.contact_types.title")}</h3>
      <p class="mt-1 text-sm text-text-muted">{t("settings.contact_types.subtitle")}</p>
    </a>
    <a
      href="/settings/branding"
      class="rounded-xl border border-border bg-surface-raised p-5 hover:border-brand"
    >
      <h3 class="text-sm font-semibold text-text">{t("settings.branding.title")}</h3>
      <p class="mt-1 text-sm text-text-muted">{t("settings.branding.subtitle")}</p>
    </a>
    <a
      href="/settings/modules"
      class="rounded-xl border border-border bg-surface-raised p-5 hover:border-brand"
    >
      <h3 class="text-sm font-semibold text-text">{t("settings.modules.title")}</h3>
      <p class="mt-1 text-sm text-text-muted">{t("settings.modules.subtitle")}</p>
    </a>
    <a
      href="/settings/notification-defaults"
      class="rounded-xl border border-border bg-surface-raised p-5 hover:border-brand"
    >
      <h3 class="text-sm font-semibold text-text">{t("settings.notification_defaults.title")}</h3>
      <p class="mt-1 text-sm text-text-muted">{t("settings.notification_defaults.subtitle")}</p>
    </a>
    <a
      href="/settings/dashboard"
      class="rounded-xl border border-border bg-surface-raised p-5 hover:border-brand"
    >
      <h3 class="text-sm font-semibold text-text">{t("settings.dashboard.title")}</h3>
      <p class="mt-1 text-sm text-text-muted">{t("settings.dashboard.subtitle")}</p>
    </a>
    <a
      href="/tasks/templates"
      class="rounded-xl border border-border bg-surface-raised p-5 hover:border-brand"
    >
      <h3 class="text-sm font-semibold text-text">{t("settings.task_templates.title")}</h3>
      <p class="mt-1 text-sm text-text-muted">{t("settings.task_templates.subtitle")}</p>
    </a>
    <a
      href="/settings/task-labels"
      class="rounded-xl border border-border bg-surface-raised p-5 hover:border-brand"
    >
      <h3 class="text-sm font-semibold text-text">{t("settings.task_labels.title")}</h3>
      <p class="mt-1 text-sm text-text-muted">{t("settings.task_labels.subtitle")}</p>
    </a>
    <a
      href="/settings/task-statuses"
      class="rounded-xl border border-border bg-surface-raised p-5 hover:border-brand"
    >
      <h3 class="text-sm font-semibold text-text">{t("settings.task_statuses.title")}</h3>
      <p class="mt-1 text-sm text-text-muted">{t("settings.task_statuses.subtitle")}</p>
    </a>
    <a
      href="/settings/leave"
      class="rounded-xl border border-border bg-surface-raised p-5 hover:border-brand"
    >
      <h3 class="text-sm font-semibold text-text">{t("settings.leave.title")}</h3>
      <p class="mt-1 text-sm text-text-muted">{t("settings.leave.subtitle")}</p>
    </a>
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
    <a
      href="/settings/system"
      class="rounded-xl border border-border bg-surface-raised p-5 hover:border-brand"
    >
      <h3 class="text-sm font-semibold text-text">{t("settings.system.title")}</h3>
      <p class="mt-1 text-sm text-text-muted">{t("settings.system.subtitle")}</p>
    </a>
  </div>
</section>
