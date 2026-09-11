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
  // The E-mailinbox is not a permanent slot (CLAUDE.md §10, the Timeon lesson): a queue that
  // is empty most days is the one people stop reading. It finds you instead — the tab is drawn
  // with the count of your mails waiting for a client, and only while there are any, or while
  // you are on it (a notification's link has to land on a page with a tab).
  const intakeWaiting = $derived(Number(page.data.intakeWaiting ?? 0));
  const showInbox = $derived(
    can(page.data.user, "tasks.task.create") &&
      (intakeWaiting > 0 || path.startsWith("/tasks/inbox")),
  );
  const tabClass = (active: boolean) =>
    `rounded-lg px-3 py-1.5 text-sm font-medium ${
      active ? "bg-brand text-white" : "text-text-muted hover:bg-surface"
    }`;
</script>

<div class="mb-4 flex items-center gap-1" data-sveltekit-preload-data="hover">
  <a href="/tasks" class={tabClass(path === "/tasks")}>{t("tasks.title")}</a>
  {#if showInbox}
    <a href="/tasks/inbox" class={tabClass(path.startsWith("/tasks/inbox"))}>
      {t("tasks.inbox.title")}
      {#if intakeWaiting > 0}
        <span
          class="ml-1 inline-flex min-w-5 items-center justify-center rounded-full bg-amber-500 px-1.5 text-xs font-semibold text-white"
          >{intakeWaiting}</span
        >
      {/if}
    </a>
  {/if}
  {#if canManageTemplates}
    <a href="/tasks/templates" class={tabClass(path.startsWith("/tasks/templates"))}>
      {t("tasks.nav.templates")}
    </a>
  {/if}
</div>
