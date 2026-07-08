<script lang="ts">
  import { t } from "$lib/core/i18n";
</script>

<svelte:head>
  <title>{t("settings.title")}</title>
</svelte:head>

<h1 class="mb-6 text-xl font-semibold text-neutral-900">{t("settings.title")}</h1>

<section class="mb-8">
  <h2 class="mb-3 text-xs font-semibold uppercase tracking-wide text-neutral-400">
    {t("settings.section_personal")}
  </h2>
  <div class="grid gap-4 sm:grid-cols-2">
    <a href="/settings/account"
      class="rounded-xl border border-neutral-200 bg-white p-5 hover:border-brand">
      <h3 class="text-sm font-semibold text-neutral-900">{t("settings.account.title")}</h3>
      <p class="mt-1 text-sm text-neutral-500">{t("settings.account.subtitle")}</p>
    </a>
  </div>
</section>

<section>
  <h2 class="mb-3 text-xs font-semibold uppercase tracking-wide text-neutral-400">
    {t("settings.section_org")}
  </h2>
  <div class="grid gap-4 sm:grid-cols-2">
    <a href="/settings/users"
      class="rounded-xl border border-neutral-200 bg-white p-5 hover:border-brand">
      <h3 class="text-sm font-semibold text-neutral-900">{t("settings.users.title")}</h3>
      <p class="mt-1 text-sm text-neutral-500">{t("settings.users.subtitle")}</p>
    </a>
    <a href="/settings/custom-fields"
      class="rounded-xl border border-neutral-200 bg-white p-5 hover:border-brand">
      <h3 class="text-sm font-semibold text-neutral-900">{t("settings.custom_fields.title")}</h3>
      <p class="mt-1 text-sm text-neutral-500">{t("settings.custom_fields.subtitle")}</p>
    </a>
    <a href="/settings/branding"
      class="rounded-xl border border-neutral-200 bg-white p-5 hover:border-brand">
      <h3 class="text-sm font-semibold text-neutral-900">{t("settings.branding.title")}</h3>
      <p class="mt-1 text-sm text-neutral-500">{t("settings.branding.subtitle")}</p>
    </a>
    <a href="/settings/modules"
      class="rounded-xl border border-neutral-200 bg-white p-5 hover:border-brand">
      <h3 class="text-sm font-semibold text-neutral-900">{t("settings.modules.title")}</h3>
      <p class="mt-1 text-sm text-neutral-500">{t("settings.modules.subtitle")}</p>
    </a>
    <a href="/settings/dashboard"
      class="rounded-xl border border-neutral-200 bg-white p-5 hover:border-brand">
      <h3 class="text-sm font-semibold text-neutral-900">{t("settings.dashboard.title")}</h3>
      <p class="mt-1 text-sm text-neutral-500">{t("settings.dashboard.subtitle")}</p>
    </a>
    <a href="/tasks/templates"
      class="rounded-xl border border-neutral-200 bg-white p-5 hover:border-brand">
      <h3 class="text-sm font-semibold text-neutral-900">{t("settings.task_templates.title")}</h3>
      <p class="mt-1 text-sm text-neutral-500">{t("settings.task_templates.subtitle")}</p>
    </a>
    <a href="/settings/task-labels"
      class="rounded-xl border border-neutral-200 bg-white p-5 hover:border-brand">
      <h3 class="text-sm font-semibold text-neutral-900">{t("settings.task_labels.title")}</h3>
      <p class="mt-1 text-sm text-neutral-500">{t("settings.task_labels.subtitle")}</p>
    </a>
  </div>
</section>
