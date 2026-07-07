<script lang="ts">
  /** Company-detail panel: projects attached to this company (CLAUDE.md §6). */
  import { t } from "$lib/core/i18n";

  let { data }: { companyId: string; data: Record<string, unknown> } = $props();

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
  <p class="text-sm text-neutral-500">{t("projects.empty")}</p>
{:else}
  <ul class="divide-y divide-neutral-100">
    {#each projects as project (project.id)}
      <li class="flex items-center justify-between py-2">
        <a
          href="/projects/{project.id}"
          class="text-sm font-medium text-neutral-900 hover:text-brand"
        >
          {project.name}
        </a>
        <span class="text-xs text-neutral-500">
          {#if project.budget_hours != null}{t("projects.budget_hours_short", {
              hours: project.budget_hours,
            })} · {/if}
          {t(`projects.status.${project.status}`)}
        </span>
      </li>
    {/each}
  </ul>
{/if}
