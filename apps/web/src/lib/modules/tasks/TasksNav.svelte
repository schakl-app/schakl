<script lang="ts">
  /** Submenu for the tasks section: the list and the shared template repository. */
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";

  const path = $derived(page.url.pathname);
  const tabClass = (active: boolean) =>
    `rounded-lg px-3 py-1.5 text-sm font-medium ${
      active ? "bg-brand text-white" : "text-neutral-600 hover:bg-neutral-100"
    }`;
</script>

<div class="mb-4 flex items-center gap-1" data-sveltekit-preload-data="hover">
  <a href="/tasks" class={tabClass(path === "/tasks")}>{t("tasks.title")}</a>
  <a href="/tasks/templates" class={tabClass(path.startsWith("/tasks/templates"))}>
    {t("tasks.nav.templates")}
  </a>
</div>
