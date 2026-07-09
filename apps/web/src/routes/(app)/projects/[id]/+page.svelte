<script lang="ts">
  import { Pencil, Trash2 } from "@lucide/svelte";
  import { dndzone } from "svelte-dnd-action";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import CustomFieldsForm from "$lib/core/customfields/CustomFieldsForm.svelte";
  import CustomFieldsView from "$lib/core/customfields/CustomFieldsView.svelte";
  import { burnBarClass, burnBarWidth, burnPct } from "$lib/core/burn";
  import { fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { entityPanelsFor } from "$lib/core/registry";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import AssigneePicker from "$lib/core/ui/AssigneePicker.svelte";
  import AvatarStack from "$lib/core/ui/AvatarStack.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import TaskRow from "$lib/modules/tasks/TaskRow.svelte";

  let { data, form } = $props();

  // Panels are contributed by enabled modules and composed here — this page never names `time`.
  const enabled = $derived(page.data.theme?.enabledModules ?? []);
  const panelSpecs = $derived(entityPanelsFor(enabled, "project"));
  const panelComponent = (key: string) => panelSpecs.find((spec) => spec.key === key)?.component;
  // The lookups this page already holds, handed down so a panel never refetches them.
  const panelLookups = $derived({
    members: data.members,
    companies: data.companies,
    projects: data.projects,
    tasks: data.tasks,
  });

  // Use mode vs edit mode (UX §3): the definition is edited behind the ⋯ → Bewerken toggle.
  let editing = $state(false);
  let confirmDelete = $state(false);

  const STATUSES = ["active", "on_hold", "completed", "archived"] as const;
  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";

  const project = $derived(data.project);
  const tasks = $derived(data.tasks);
  const doneCount = $derived(tasks.filter((t) => t.status === "done").length);

  const assignees = $derived(project.assignees ?? []);

  // Drag-to-reorder: local mirror of the task list for the dnd zone; a drop PATCHes the
  // moved task's position to the fractional midpoint of its new neighbours.
  type TaskItem = (typeof data.tasks)[number];
  let dndItems = $state<TaskItem[]>([]);
  $effect(() => {
    dndItems = [...data.tasks];
  });
  let reorderForm: HTMLFormElement | undefined = $state();
  let reorderId = $state("");
  let reorderPosition = $state(0);

  function handleDndConsider(e: CustomEvent<{ items: TaskItem[] }>) {
    dndItems = e.detail.items;
  }
  function handleDndFinalize(e: CustomEvent<{ items: TaskItem[]; info: { id: string } }>) {
    dndItems = e.detail.items;
    const movedId = e.detail.info.id;
    const index = dndItems.findIndex((item) => item.id === movedId);
    if (index === -1) return;
    const prev = dndItems[index - 1]?.position;
    const next = dndItems[index + 1]?.position;
    let position: number;
    if (prev != null && next != null) position = (prev + next) / 2;
    else if (prev != null) position = prev + 1024;
    else if (next != null) position = next - 1024;
    else return;
    reorderId = movedId;
    reorderPosition = position;
    // Submit on the next tick so the hidden inputs carry the fresh values.
    setTimeout(() => reorderForm?.requestSubmit(), 0);
  }
  const companyName = $derived(
    project.company_id ? (data.companies.find((c) => c.id === project.company_id)?.name ?? "") : "",
  );
  // Planned billable value from the hours budget × rate (fallback to the amount budget).
  const plannedValue = $derived(
    project.budget_hours != null && project.hourly_rate != null
      ? project.budget_hours * project.hourly_rate
      : (project.budget_amount ?? null),
  );

  // Actuals from logged time (team-wide) against the budget, computed by the API over the budget's
  // current period — the same figures the projects list column shows, and the same period the Uren
  // panel below lists the entries for.
  const loggedHours = $derived(project.hours?.spent_hours ?? 0);
  const billableValue = $derived(
    project.hourly_rate != null ? (project.hours?.billable_hours ?? 0) * project.hourly_rate : null,
  );
  // The one burn scale (core/burn.ts, docs/UX.md). Unclamped: this used to `Math.min(100, …)`,
  // so a project 40 % over budget drew exactly like one that had just landed on it.
  const budgetPct = $derived(burnPct(loggedHours, project.budget_hours));

  const money = (n: number) =>
    new Intl.NumberFormat("nl-NL", {
      style: "currency",
      currency: project.currency || "EUR",
    }).format(n);
</script>

<svelte:head>
  <title>{project.name}</title>
</svelte:head>

<div class="mb-6 flex items-start justify-between">
  <div>
    <a href="/projects" class="text-sm text-text-muted hover:text-text">← {t("projects.title")}</a>
    <h1 class="mt-1 text-xl font-semibold text-text">{project.name}</h1>
    <p class="mt-1 text-sm text-text-muted">
      {#if companyName}{companyName} ·
      {/if}{t(`projects.status.${project.status}`)}
      {#if assignees.length > 0}
        · {t("projects.field.responsible")}:
        <AvatarStack {assignees} members={data.members} />
      {/if}
    </p>
  </div>
  <ActionsMenu
    items={[
      {
        label: editing ? t("common.cancel") : t("common.edit"),
        icon: Pencil,
        onclick: () => (editing = !editing),
      },
      {
        label: t("common.delete"),
        icon: Trash2,
        danger: true,
        onclick: () => (confirmDelete = true),
      },
    ]}
  />
</div>

<div class="grid gap-4 lg:grid-cols-2">
  <!-- Budget overview -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="mb-4 text-sm font-semibold text-text">{t("projects.budget")}</h2>
    <dl class="grid grid-cols-2 gap-4 text-sm">
      <div>
        <dt class="text-text-muted">{t("projects.field.budget_hours")}</dt>
        <dd class="mt-0.5 font-medium text-text">
          {project.budget_hours != null
            ? `${project.budget_hours} ${t("projects.hours_unit")}`
            : "—"}
        </dd>
      </div>
      <div>
        <dt class="text-text-muted">{t("projects.field.hourly_rate")}</dt>
        <dd class="mt-0.5 font-medium text-text">
          {project.hourly_rate != null ? money(project.hourly_rate) : "—"}
        </dd>
      </div>
      <div>
        <dt class="text-text-muted">{t("projects.planned_value")}</dt>
        <dd class="mt-0.5 font-medium text-text">
          {plannedValue != null ? money(plannedValue) : "—"}
        </dd>
      </div>
      <div>
        <dt class="text-text-muted">{t("projects.field.billable_default")}</dt>
        <dd class="mt-0.5 font-medium text-text">
          {project.billable_default ? t("common.yes") : t("common.no")}
        </dd>
      </div>
    </dl>
    <div class="mt-4 border-t border-border pt-4">
      <div class="flex items-end justify-between text-sm">
        <span class="text-text-muted"
          >{t(`projects.logged_period.${project.budget_period ?? "total"}`)}</span
        >
        <span class="font-medium text-text">
          {loggedHours}
          {t("projects.hours_unit")}{#if project.budget_hours != null}
            <span class="text-text-muted">
              / {project.budget_hours} {t("projects.hours_unit")}</span
            >
          {/if}
        </span>
      </div>
      {#if budgetPct != null}
        <div class="mt-1.5 h-2 overflow-hidden rounded-full bg-surface">
          <!-- The number may exceed 100 %; the bar it draws cannot. -->
          <div
            class="h-full rounded-full {burnBarClass(budgetPct)}"
            style="width: {burnBarWidth(budgetPct)}%"
          ></div>
        </div>
      {/if}
      {#if billableValue != null}
        <div class="mt-3 flex items-center justify-between text-sm">
          <span class="text-text-muted">{t("projects.billable_value")}</span>
          <span class="font-medium text-text">{money(billableValue)}</span>
        </div>
      {/if}
    </div>
  </section>

  <!-- Details (use mode) / Edit (edit mode) -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    {#if editing}
      <h2 class="mb-4 text-sm font-semibold text-text">{t("common.edit")}</h2>
      <form
        method="POST"
        action="?/update"
        use:enhance={() =>
          ({ update }) => {
            editing = false;
            void update();
          }}
        class="space-y-3"
      >
        <div>
          <label for="edit-name" class="mb-1 block text-sm font-medium text-text"
            >{t("projects.field.name")}</label
          >
          <input id="edit-name" name="name" value={project.name} required class={inputClass} />
        </div>
        <div>
          <span class="mb-1 block text-sm font-medium text-text"
            >{t("projects.field.assignees")}</span
          >
          <AssigneePicker
            members={data.members}
            value={assignees}
            id="edit-project-assignees"
            placeholder={t("assignees.add")}
          />
        </div>
        <div class="grid grid-cols-2 gap-3">
          <div>
            <label for="status" class="mb-1 block text-sm font-medium text-text"
              >{t("projects.field.status")}</label
            >
            <select id="status" name="status" class={inputClass}>
              {#each STATUSES as s (s)}
                <option value={s} selected={project.status === s}
                  >{t(`projects.status.${s}`)}</option
                >
              {/each}
            </select>
          </div>
          <div class="flex items-center gap-2 pt-6">
            <input
              id="billable_default"
              name="billable_default"
              type="checkbox"
              checked={project.billable_default}
              class="h-4 w-4 rounded border-border text-brand focus:ring-brand"
            />
            <label for="billable_default" class="text-sm font-medium text-text"
              >{t("projects.field.billable_default")}</label
            >
          </div>
          <div>
            <label for="budget_hours" class="mb-1 block text-sm font-medium text-text"
              >{t("projects.field.budget_hours")}</label
            >
            <input
              id="budget_hours"
              name="budget_hours"
              type="number"
              min="0"
              step="0.5"
              value={project.budget_hours ?? ""}
              class={inputClass}
            />
          </div>
          <div>
            <label for="budget_period" class="mb-1 block text-sm font-medium text-text"
              >{t("projects.field.budget_period")}</label
            >
            <select id="budget_period" name="budget_period" class={inputClass}>
              {#each ["total", "monthly", "weekly", "daily"] as period (period)}
                <option value={period} selected={(project.budget_period ?? "total") === period}>
                  {t(`projects.budget_period.${period}`)}
                </option>
              {/each}
            </select>
          </div>
          <div>
            <label for="hourly_rate" class="mb-1 block text-sm font-medium text-text"
              >{t("projects.field.hourly_rate")}</label
            >
            <input
              id="hourly_rate"
              name="hourly_rate"
              type="number"
              min="0"
              step="0.01"
              value={project.hourly_rate ?? ""}
              class={inputClass}
            />
          </div>
          <div>
            <label for="budget_amount" class="mb-1 block text-sm font-medium text-text"
              >{t("projects.field.budget_amount")}</label
            >
            <input
              id="budget_amount"
              name="budget_amount"
              type="number"
              min="0"
              step="0.01"
              value={project.budget_amount ?? ""}
              class={inputClass}
            />
          </div>
          <div>
            <label for="start_date" class="mb-1 block text-sm font-medium text-text"
              >{t("projects.field.start_date")}</label
            >
            <DateInput name="start_date" value={project.start_date ?? ""} />
          </div>
          <div>
            <label for="end_date" class="mb-1 block text-sm font-medium text-text"
              >{t("projects.field.end_date")}</label
            >
            <DateInput name="end_date" value={project.end_date ?? ""} />
          </div>
        </div>
        <div>
          <label for="edit-description" class="mb-1 block text-sm font-medium text-text"
            >{t("projects.field.description")}</label
          >
          <textarea id="edit-description" name="description" rows="3" class={inputClass}
            >{project.description ?? ""}</textarea
          >
        </div>
        {#if data.definitions.length > 0}
          <CustomFieldsForm
            definitions={data.definitions}
            values={project.custom ?? {}}
            locale={data.locale}
          />
        {:else}
          <input type="hidden" name="custom" value={JSON.stringify(project.custom ?? {})} />
        {/if}
        {#if form?.error}<p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>{/if}
        <div class="flex justify-end gap-2 pt-1">
          <button
            type="button"
            class="rounded-lg border border-border px-4 py-2 text-sm"
            onclick={() => (editing = false)}
          >
            {t("common.cancel")}
          </button>
          <button
            class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
            >{t("common.save")}</button
          >
        </div>
      </form>
    {:else}
      <h2 class="mb-4 text-sm font-semibold text-text">{t("projects.details")}</h2>
      <dl class="grid grid-cols-2 gap-4 text-sm">
        <div>
          <dt class="text-text-muted">{t("projects.field.company")}</dt>
          <dd class="mt-0.5 font-medium text-text">{companyName || "—"}</dd>
        </div>
        <div>
          <dt class="text-text-muted">{t("projects.field.responsible")}</dt>
          <dd class="mt-0.5 font-medium text-text">
            {#if assignees.length > 0}
              <AvatarStack {assignees} members={data.members} />
            {:else}
              —
            {/if}
          </dd>
        </div>
        <div>
          <dt class="text-text-muted">{t("projects.field.start_date")}</dt>
          <dd class="mt-0.5 font-medium text-text">
            {project.start_date ? fmtNumericDate(project.start_date) : "—"}
          </dd>
        </div>
        <div>
          <dt class="text-text-muted">{t("projects.field.end_date")}</dt>
          <dd class="mt-0.5 font-medium text-text">
            {project.end_date ? fmtNumericDate(project.end_date) : "—"}
          </dd>
        </div>
        {#if project.description}
          <div class="col-span-2">
            <dt class="text-text-muted">{t("projects.field.description")}</dt>
            <dd class="mt-0.5 whitespace-pre-line text-text">{project.description}</dd>
          </div>
        {/if}
      </dl>
      {#if data.definitions.length > 0}
        <div class="mt-4 border-t border-border pt-4">
          <CustomFieldsView
            definitions={data.definitions}
            values={project.custom ?? {}}
            locale={data.locale}
          />
        </div>
      {/if}
    {/if}
  </section>
</div>

<!-- Panels contributed by enabled modules — the Uren panel answers the budget bar above it.
     Every number opens (docs/UX.md principle 7). -->
{#each data.panels as panel (panel.key)}
  {@const PanelComponent = panelComponent(panel.key)}
  {#if PanelComponent}
    <section class="mt-4 rounded-xl border border-border bg-surface-raised p-5">
      <h2 class="mb-3 text-sm font-semibold text-text">{t(panel.titleKey)}</h2>
      <PanelComponent data={panel.data} context={data.context} lookups={panelLookups} />
    </section>
  {/if}
{/each}

<!-- To-dos -->
<section class="mt-4 rounded-xl border border-border bg-surface-raised p-5">
  <div class="mb-4 flex items-center justify-between">
    <h2 class="text-sm font-semibold text-text">{t("projects.todos")}</h2>
    <span class="text-xs text-text-muted"
      >{t("projects.todos_progress", { done: doneCount, total: tasks.length })}</span
    >
  </div>

  <form method="POST" action="?/addTask" use:enhance class="mb-4 flex gap-2">
    <input type="hidden" name="company_id" value={project.company_id ?? ""} />
    <input name="title" placeholder={t("projects.add_todo")} required class={inputClass} />
    <button
      class="shrink-0 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
      >{t("common.create")}</button
    >
  </form>

  {#if tasks.length === 0}
    <p class="text-sm text-text-muted">{t("tasks.empty")}</p>
  {:else}
    <form method="POST" action="?/reorderTask" use:enhance bind:this={reorderForm} class="hidden">
      <input type="hidden" name="id" value={reorderId} />
      <input type="hidden" name="position" value={reorderPosition} />
    </form>
    <div
      class="divide-y divide-border"
      use:dndzone={{ items: dndItems, flipDurationMs: 150, dropTargetStyle: {} }}
      onconsider={handleDndConsider}
      onfinalize={handleDndFinalize}
    >
      {#each dndItems as task (task.id)}
        <div class="flex items-center bg-surface-raised">
          <span class="cursor-grab pl-1 text-text-muted hover:text-text-muted" aria-hidden="true"
            >⋮⋮</span
          >
          <div class="flex-1">
            <TaskRow {task} toggleAction="?/toggleTask" members={data.members} />
          </div>
        </div>
      {/each}
    </div>
  {/if}
</section>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("common.delete")}
  message={t("projects.delete_confirm", { name: project.name })}
  action="?/deleteProject"
/>
