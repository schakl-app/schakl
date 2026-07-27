<script lang="ts">
  /** Submenu for the tasks section: the list and the shared template repository. */
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";

  const path = $derived(page.url.pathname);
  // The repository page is a management surface for two org-wide libraries: task automation
  // (`tasks.template.write`) and checklists (`tasks.checklist_template.write`). It is not a
  // read-only view of anything — every control on it writes. A portal client (#193) reaches
  // /tasks for their own companies' tasks, so the tab sat one click away from them, offering a
  // "＋ nieuw sjabloon" form the API refuses (#244); a plain member had the same dead form.
  // The route redirects the same holders, and the API is the boundary either way (CLAUDE.md §15).
  const canManageTemplates = $derived(
    can(page.data.user, "tasks.template.write") ||
      can(page.data.user, "tasks.checklist_template.write"),
  );
  const tabClass = (active: boolean) =>
    `rounded-lg px-3 py-1.5 text-sm font-medium ${
      active ? "bg-brand text-white" : "text-text-muted hover:bg-surface"
    }`;
</script>

<div class="mb-4 flex items-center gap-1" data-sveltekit-preload-data="hover">
  <a href="/tasks" class={tabClass(path === "/tasks")}>{t("tasks.title")}</a>
  {#if canManageTemplates}
    <a href="/tasks/templates" class={tabClass(path.startsWith("/tasks/templates"))}>
      {t("tasks.nav.templates")}
    </a>
  {/if}
</div>
