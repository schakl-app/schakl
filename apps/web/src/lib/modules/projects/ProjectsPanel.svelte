<script lang="ts">
  /** Company-detail panel: projects attached to this company (CLAUDE.md §6). */
  import { ChevronDown, ChevronRight } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { fromHref } from "$lib/core/origin";
  import { can } from "$lib/core/permissions";
  import PanelRows from "$lib/core/ui/PanelRows.svelte";

  let { companyId, data }: { companyId: string; data: Record<string, unknown> } = $props();

  interface PanelProject {
    id: string;
    name: string;
    status: string;
    billable_default: boolean;
    budget_hours: number | null;
  }
  const projects = $derived((data.projects ?? []) as PanelProject[]);
  // The API has always sent the whole count and this component has never read it (#407): five
  // projects under a client who runs sixty read as the complete answer.
  const total = $derived((data.total as number | undefined) ?? projects.length);

  // Delivered and archived work is a register, not the working list (the picker rule,
  // docs/UX.md): it stays on the panel — the rows are real — but folded behind its own line,
  // so a long-standing client's card leads with what is actually running.
  const RETIRED = new Set(["completed", "archived"]);
  const live = $derived(projects.filter((p) => !RETIRED.has(p.status)));
  const retired = $derived(projects.filter((p) => RETIRED.has(p.status)));
  let showRetired = $state(false);
</script>

{#snippet row(project: PanelProject)}
  <li class="flex items-center justify-between py-2">
    <a
      href={fromHref(`/projects/${project.id}`, page.url)}
      class="text-sm font-medium text-text hover:text-brand"
    >
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
{/snippet}

<PanelRows
  rows={projects}
  {total}
  href={`/projects?company=${companyId}`}
  linkLabel={t("projects.panel.view_all", { count: total })}
>
  {#snippet children(shown)}
    {#if shown.length === 0}
      <p class="text-sm text-text-muted">{t("projects.empty")}</p>
    {:else}
      {@const shownIds = new Set(shown.map((p) => p.id))}
      {@const shownLive = live.filter((p) => shownIds.has(p.id))}
      {@const shownRetired = retired.filter((p) => shownIds.has(p.id))}
      <ul class="divide-y divide-border">
        {#each shownLive as project (project.id)}
          {@render row(project)}
        {/each}
      </ul>
      {#if shownRetired.length > 0}
        <button
          type="button"
          class="mt-1 flex items-center gap-1 text-xs text-text-muted hover:text-text"
          onclick={() => (showRetired = !showRetired)}
        >
          {#if showRetired}<ChevronDown size={14} aria-hidden="true" />{:else}<ChevronRight
              size={14}
              aria-hidden="true"
            />{/if}
          {t("projects.panel.retired", { count: shownRetired.length })}
        </button>
        {#if showRetired}
          <ul class="divide-y divide-border">
            {#each shownRetired as project (project.id)}
              {@render row(project)}
            {/each}
          </ul>
        {/if}
      {/if}
    {/if}
  {/snippet}
  {#snippet actions()}
    {#if can(page.data.user, "projects.project.write")}
      <!-- Quick-create from the client page (create-then-edit, same as tasks #230): a POST —
             never a link, which would create on hover-preload — that makes a minimal project
             pre-linked to this client, then lands on its detail page in edit mode. -->
      <form method="POST" action="/projects?/create" use:enhance class="contents">
        <input type="hidden" name="company_id" value={companyId} />
        <button class="text-brand hover:underline">＋ {t("projects.new")}</button>
      </form>
    {/if}
  {/snippet}
</PanelRows>
