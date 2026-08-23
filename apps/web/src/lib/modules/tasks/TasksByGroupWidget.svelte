<script lang="ts">
  /** Dashboard widget: open tasks grouped per project (fallback: per client). */
  import { t } from "$lib/core/i18n";
  import { ALL_ASSIGNEES } from "$lib/modules/tasks/filters";
  import Card from "$lib/core/ui/Card.svelte";
  import StateMark from "$lib/core/ui/StateMark.svelte";

  let { data }: { data: unknown } = $props();

  interface Group {
    entity_type: string;
    entity_id: string | null;
    label: string | null;
    /** The client behind a *project* row — a project name alone does not say whose it is. */
    company_id?: string | null;
    company_name?: string | null;
    count: number;
    overdue: number;
  }
  const groups = $derived((data ?? []) as Group[]);

  // Every row on this tile opens something. The name is the record it names; the count is that
  // record's own filtered task list (issue #15). The bucket of tasks hanging off neither a client
  // nor a project has no record to open, so *both* of its links are the filtered list — the
  // `unlinked` filter exists precisely so this row is addressable rather than dumping the reader
  // on an unfiltered /tasks and letting them hunt.
  const isUnlinked = (group: Group) => group.entity_type === "none" || group.entity_id == null;

  const listHref = (group: Group) => {
    const filter = isUnlinked(group)
      ? "unlinked=1&"
      : group.entity_type === "project"
        ? `project_id=${group.entity_id}&`
        : `company_id=${group.entity_id}&`;
    return `/tasks?${filter}assignee_user_id=${ALL_ASSIGNEES}`;
  };
  const entityHref = (group: Group) =>
    isUnlinked(group)
      ? listHref(group)
      : group.entity_type === "project"
        ? `/projects/${group.entity_id}`
        : `/companies/${group.entity_id}`;

  // The bucket says what it is. It used to borrow `time.general` ("Algemeen") — a word a tenant
  // is just as likely to have named a real project, so the tile drew "Algemeen" twice and the
  // fallback was indistinguishable from the record. Same words as the list's own filter chip.
  const groupName = (group: Group) =>
    isUnlinked(group) ? t("tasks.filter.unlinked") : (group.label ?? "—");
</script>

<Card
  title={t("dashboard.open_by_group.title")}
  href="/tasks?assignee_user_id={ALL_ASSIGNEES}"
  linkLabel={t("nav.tasks")}
>
  {#if groups.length === 0}
    <p class="text-sm text-text-muted">{t("dashboard.open_by_group.empty")}</p>
  {:else}
    <ul class="divide-y divide-border">
      {#each groups as group (`${group.entity_type}:${group.entity_id}`)}
        <li class="flex items-center justify-between gap-2 py-2">
          <a href={entityHref(group)} class="group min-w-0 flex-1">
            <span class="block truncate text-sm font-medium text-text group-hover:text-brand"
              >{groupName(group)}</span
            >
            {#if group.entity_type === "project" && group.company_name}
              <!-- Two clients may each run a project called "Website": without the client the
                   rows are indistinguishable and only opening one tells them apart. -->
              <span class="block truncate text-xs text-text-muted">{group.company_name}</span>
            {/if}
          </a>
          {#if group.overdue > 0}
            <!-- One shade of one claim (#404): the chip and the figure it sits beside read the
                 same red everywhere, and the glyph is what carries it in greyscale. -->
            <a href="{listHref(group)}&due=overdue" class="shrink-0 hover:underline">
              <StateMark
                state="late"
                variant="chip"
                label={t("tasks.overdue_count", { count: group.overdue })}
              />
            </a>
          {/if}
          <a
            href={listHref(group)}
            class="shrink-0 rounded-full bg-surface px-2 py-0.5 text-xs font-semibold tabular-nums text-text-muted hover:text-brand"
          >
            {group.count}
          </a>
        </li>
      {/each}
    </ul>
  {/if}
</Card>
