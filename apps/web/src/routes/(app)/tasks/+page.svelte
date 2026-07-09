<script lang="ts">
  import { enhance } from "$app/forms";
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import SearchInput from "$lib/core/ui/SearchInput.svelte";
  import { labelChipClass } from "$lib/modules/tasks/labels";
  import TaskRow from "$lib/modules/tasks/TaskRow.svelte";
  import TasksNav from "$lib/modules/tasks/TasksNav.svelte";

  let { data, form } = $props();

  let showCreate = $state(false);
  let showDone = $state(false);
  const userId = $derived(page.data.user?.id ?? "");

  const priorities = ["low", "normal", "high"] as const;
  const dueOptions = ["overdue", "today", "week"] as const;

  const today = new Date().toISOString().slice(0, 10);
  const open = $derived(data.tasks.filter((task) => task.status === "open"));
  const inProgress = $derived(data.tasks.filter((task) => task.status === "in_progress"));
  const done = $derived(data.tasks.filter((task) => task.status === "done"));
  const overdueCount = $derived(
    data.tasks.filter((task) => task.status !== "done" && task.due_date && task.due_date < today)
      .length,
  );

  const companyItems = $derived(data.companies.map((c) => ({ value: c.id, label: c.name })));
  const projectItems = $derived(data.projects.map((p) => ({ value: p.id, label: p.name })));
  const memberItems = $derived(
    data.members.map((m) => ({ value: m.user_id, label: m.full_name || m.email })),
  );

  // Create-form state: the project pick narrows to that project's client automatically.
  let fCompany = $state("");
  let fProject = $state("");
  const createProjects = $derived(
    fCompany
      ? data.projects.filter((p) => p.company_id === fCompany || !p.company_id)
      : data.projects,
  );
  function onProjectPicked(projectId: string) {
    const project = data.projects.find((p) => p.id === projectId);
    if (project?.company_id) fCompany = project.company_id;
  }

  function setFilter(key: string, value: string) {
    const url = new URL(page.url);
    if (value) url.searchParams.set(key, value);
    else url.searchParams.delete(key);
    void goto(url, { keepFocus: true, noScroll: true });
  }
  const hasFilters = $derived(Object.values(data.filters).some(Boolean));

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<svelte:head>
  <title>{t("tasks.title")}</title>
</svelte:head>

<TasksNav />

<div class="mb-6 flex items-center justify-between">
  <div>
    <h1 class="text-xl font-semibold text-text">{t("tasks.title")}</h1>
    <p class="mt-1 text-sm text-text-muted">
      {t("tasks.count", { count: data.total })}
      {#if overdueCount > 0}
        · <span class="font-medium text-red-600 dark:text-red-400"
          >{t("tasks.overdue_count", { count: overdueCount })}</span
        >
      {/if}
    </p>
  </div>
  <button
    class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
    onclick={() => (showCreate = !showCreate)}
  >
    {t("tasks.new")}
  </button>
</div>

<!-- Filter bar -->
<div class="mb-4 flex flex-wrap items-center gap-2">
  <SearchInput placeholder={t("tasks.search_placeholder")} />
  <div class="w-44">
    <Combobox
      items={companyItems}
      name="_filter_company"
      value={data.filters.company_id ?? ""}
      placeholder={t("tasks.field.company")}
      onselect={(v) => setFilter("company_id", v)}
      id="filter-company"
    />
  </div>
  <div class="w-44">
    <Combobox
      items={projectItems}
      name="_filter_project"
      value={data.filters.project_id ?? ""}
      placeholder={t("tasks.field.project")}
      onselect={(v) => setFilter("project_id", v)}
      id="filter-project"
    />
  </div>
  <div class="w-44">
    <Combobox
      items={memberItems}
      name="_filter_assignee"
      value={data.filters.assignee_user_id ?? ""}
      placeholder={t("tasks.field.assignee")}
      onselect={(v) => setFilter("assignee_user_id", v)}
      id="filter-assignee"
    />
  </div>
  {#each dueOptions as option (option)}
    <button
      class="rounded-full px-3 py-1 text-xs font-medium
        {data.filters.due === option
        ? 'bg-brand text-white'
        : 'border border-border text-text-muted hover:border-brand hover:text-brand'}"
      onclick={() => setFilter("due", data.filters.due === option ? "" : option)}
      >{t(`tasks.due.${option}`)}</button
    >
  {/each}
  {#each data.labels as label (label.id)}
    <button
      class="rounded-full px-3 py-1 text-xs font-medium
        {data.filters.label_id === label.id ? 'ring-2 ring-brand ' : ''}{labelChipClass(
        label.color,
      )}"
      onclick={() => setFilter("label_id", data.filters.label_id === label.id ? "" : label.id)}
      >{label.name}</button
    >
  {/each}
  {#if hasFilters}
    <a href="/tasks" class="text-xs text-text-muted underline hover:text-text"
      >{t("tasks.filter.clear")}</a
    >
  {/if}
</div>

{#if showCreate}
  <form
    method="POST"
    action="?/create"
    use:enhance={() =>
      ({ update }) => {
        void update().then(() => (showCreate = false));
      }}
    class="mb-6 rounded-xl border border-border bg-surface-raised p-4"
  >
    <div class="grid gap-3 sm:grid-cols-2">
      <div class="sm:col-span-2">
        <label for="title" class="mb-1 block text-sm font-medium text-text"
          >{t("tasks.field.title")}</label
        >
        <input id="title" name="title" required class={inputClass} />
      </div>
      <div class="sm:col-span-2">
        <label for="description" class="mb-1 block text-sm font-medium text-text"
          >{t("tasks.field.description")}</label
        >
        <textarea id="description" name="description" rows="2" class={inputClass}></textarea>
      </div>
      <div>
        <label for="create-project" class="mb-1 block text-sm font-medium text-text"
          >{t("tasks.field.project")}</label
        >
        <Combobox
          items={createProjects.map((p) => ({ value: p.id, label: p.name }))}
          name="project_id"
          bind:value={fProject}
          id="create-project"
          onselect={onProjectPicked}
        />
      </div>
      <div>
        <label for="create-company" class="mb-1 block text-sm font-medium text-text"
          >{t("tasks.field.company")}</label
        >
        <Combobox
          items={companyItems}
          name="company_id"
          bind:value={fCompany}
          id="create-company"
        />
      </div>
      <div>
        <label for="create-assignee" class="mb-1 block text-sm font-medium text-text"
          >{t("tasks.field.assignee")}</label
        >
        <Combobox items={memberItems} name="assignee_user_id" value={userId} id="create-assignee" />
      </div>
      <div class="grid grid-cols-2 gap-3">
        <div>
          <label for="priority" class="mb-1 block text-sm font-medium text-text"
            >{t("tasks.field.priority")}</label
          >
          <select id="priority" name="priority" class={inputClass}>
            {#each priorities as p (p)}
              <option value={p} selected={p === "normal"}>{t(`tasks.priority.${p}`)}</option>
            {/each}
          </select>
        </div>
        <div>
          <label for="due_date" class="mb-1 block text-sm font-medium text-text"
            >{t("tasks.field.due_date")}</label
          >
          <DateInput id="due_date" name="due_date" />
        </div>
      </div>
    </div>
    {#if form?.error}<p class="mt-2 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>{/if}
    <div class="mt-4 flex gap-2">
      <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >{t("common.save")}</button
      >
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm"
        onclick={() => (showCreate = false)}>{t("common.cancel")}</button
      >
    </div>
  </form>
{/if}

{#if data.tasks.length === 0}
  <div class="rounded-xl border border-dashed border-border bg-surface-raised p-10 text-center">
    <p class="font-medium text-text">{t("tasks.empty")}</p>
    <p class="mt-1 text-sm text-text-muted">{t("tasks.empty_hint")}</p>
  </div>
{:else}
  <div class="space-y-4">
    {#each [{ key: "open", rows: open }, { key: "in_progress", rows: inProgress }] as group (group.key)}
      {#if group.rows.length > 0}
        <section class="overflow-hidden rounded-xl border border-border bg-surface-raised">
          <h2
            class="border-b border-border bg-surface px-4 py-2 text-xs font-semibold uppercase tracking-wide text-text-muted"
          >
            {t(`tasks.group.${group.key}`)} · {group.rows.length}
          </h2>
          <div class="divide-y divide-border">
            {#each group.rows as task (task.id)}
              <TaskRow {task} members={data.members} {today} />
            {/each}
          </div>
        </section>
      {/if}
    {/each}

    {#if done.length > 0}
      <section class="overflow-hidden rounded-xl border border-border bg-surface-raised">
        <button
          class="flex w-full items-center justify-between border-b border-border bg-surface px-4 py-2 text-xs font-semibold uppercase tracking-wide text-text-muted hover:text-text"
          onclick={() => (showDone = !showDone)}
        >
          <span>{t("tasks.group.done")} · {done.length}</span>
          <span>{showDone ? "▾" : "▸"}</span>
        </button>
        {#if showDone}
          <div class="divide-y divide-border">
            {#each done as task (task.id)}
              <TaskRow {task} members={data.members} {today} />
            {/each}
          </div>
        {/if}
      </section>
    {/if}
  </div>
{/if}
