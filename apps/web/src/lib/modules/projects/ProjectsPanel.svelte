<script lang="ts">
  /** Company-detail panel: projects attached to this company (CLAUDE.md §6). */
  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";

  let { companyId, data }: { companyId: string; data: Record<string, unknown> } = $props();

  interface PanelProject {
    id: string;
    name: string;
    status: string;
    billable_default: boolean;
    budget_hours: number | null;
  }
  const projects = $derived((data.projects ?? []) as PanelProject[]);
</script>

{#if projects.length === 0}
  <p class="text-sm text-text-muted">{t("projects.empty")}</p>
{:else}
  <ul class="divide-y divide-border">
    {#each projects as project (project.id)}
      <li class="flex items-center justify-between py-2">
        <a href="/projects/{project.id}" class="text-sm font-medium text-text hover:text-brand">
          {project.name}
        </a>
        <span class="text-xs text-text-muted">
          {#if project.budget_hours != null}{t("projects.budget_hours_short", {
              hours: project.budget_hours,
            })} ·
          {/if}
          {t(`projects.status.${project.status}`)}
        </span>
      </li>
    {/each}
  </ul>
{/if}
{#if can(page.data.user, "projects.project.write")}
  <!-- Quick-create from the client page (create-then-edit, same as tasks #230): a POST — never
       a link, which would create on hover-preload — that makes a minimal project pre-linked to
       this client, then lands on its detail page in edit mode. -->
  <form method="POST" action="/projects?/create" use:enhance class="mt-3">
    <input type="hidden" name="company_id" value={companyId} />
    <button class="text-xs text-brand hover:underline">＋ {t("projects.new")}</button>
  </form>
{/if}
