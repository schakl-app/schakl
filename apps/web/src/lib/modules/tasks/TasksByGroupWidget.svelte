<script lang="ts">
  /** Dashboard widget: open tasks grouped per project (fallback: per client). */
  import { t } from "$lib/core/i18n";
  import { ALL_ASSIGNEES } from "$lib/modules/tasks/filters";
  import { urgencyCounters } from "$lib/modules/tasks/urgency";
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
    /** The urgency partition (#398). Disjoint, and each one is a `?due=` chip's own set. */
    overdue: number;
    due_today: number;
    due_week: number;
  }
  interface Payload {
    groups: Group[];
    /** How many groups there are, so a capped tile can say what it is not showing. */
    total: number;
  }
  const payload = $derived((data ?? { groups: [], total: 0 }) as Payload);
  const groups = $derived(payload.groups ?? []);
  const notShown = $derived(Math.max(0, (payload.total ?? 0) - groups.length));

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

  // The counter row, in urgency order (#398): one entry per non-empty bucket, each a link into
  // exactly the set it counted. The partition itself, and which `?due=` list each bucket opens,
  // lives in `urgency.ts` — a mapping that renders plausibly whichever way round it is wired
  // is one that needs a test rather than a reader.
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
        <li class="flex flex-wrap items-center gap-x-2 gap-y-1 py-2">
          <!-- A floor under the name, not `min-w-0`: on a phone two counters and a total left
               "Projecte…" over "Bouwbedr…", which is a row that can be seen and not read. With
               a floor the counters wrap onto their own line instead, and a quiet row (one
               figure, no counters) still fits on one. -->
          <a href={entityHref(group)} class="group min-w-[7rem] flex-1">
            <span class="block truncate text-sm font-medium text-text group-hover:text-brand"
              >{groupName(group)}</span
            >
            {#if group.entity_type === "project" && group.company_name}
              <!-- Two clients may each run a project called "Website": without the client the
                   rows are indistinguishable and only opening one tells them apart. -->
              <span class="block truncate text-xs text-text-muted">{group.company_name}</span>
            {/if}
          </a>
          {#each urgencyCounters(group) as counter (counter.due)}
            <!-- One shade of one claim (#404): the chip and the figure it sits beside read the
                 same colour everywhere, and the glyph is what carries it in greyscale. -->
            <a href="{listHref(group)}&due={counter.due}" class="shrink-0 hover:underline">
              <StateMark
                state={counter.state}
                variant="chip"
                label={t(counter.key, { count: counter.count })}
              />
            </a>
          {/each}
          <!-- The total stays last and stays muted: "how much is there" is still a question,
               just no longer the first one. -->
          <a
            href={listHref(group)}
            class="shrink-0 rounded-full bg-surface px-2 py-0.5 text-xs font-semibold tabular-nums text-text-muted hover:text-brand"
          >
            {group.count}
          </a>
        </li>
      {/each}
    </ul>
    {#if notShown > 0}
      <!-- A short list that looks complete reads as "that is all of them" (CLAUDE.md §17), and
           this one grows with the client book. The remainder is named, and it opens the list. -->
      <a
        href="/tasks?assignee_user_id={ALL_ASSIGNEES}"
        class="mt-2 block text-xs text-text-muted hover:text-brand hover:underline"
      >
        {t("dashboard.open_by_group.more", { count: notShown })}
      </a>
    {/if}
  {/if}
</Card>
