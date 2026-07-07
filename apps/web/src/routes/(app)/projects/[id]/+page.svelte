<script lang="ts">
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { hoursFromMinutes } from "$lib/modules/time/format";

  let { data, form } = $props();

  const STATUSES = ["active", "on_hold", "completed", "archived"] as const;
  const inputClass =
    "w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";

  const project = $derived(data.project);
  const tasks = $derived(data.tasks);
  const doneCount = $derived(tasks.filter((t) => t.status === "done").length);
  const companyName = $derived(
    project.company_id
      ? (data.companies.find((c) => c.id === project.company_id)?.name ?? "")
      : "",
  );
  // Planned billable value from the hours budget × rate (fallback to the amount budget).
  const plannedValue = $derived(
    project.budget_hours != null && project.hourly_rate != null
      ? project.budget_hours * project.hourly_rate
      : (project.budget_amount ?? null),
  );

  // Actuals from logged time (team-wide) against the budget.
  const loggedHours = $derived(hoursFromMinutes(data.logged.minutes));
  const billableValue = $derived(
    project.hourly_rate != null ? (data.logged.billable_minutes / 60) * project.hourly_rate : null,
  );
  const budgetPct = $derived(
    project.budget_hours ? Math.min(100, Math.round((loggedHours / project.budget_hours) * 100)) : null,
  );

  const money = (n: number) =>
    new Intl.NumberFormat("nl-NL", { style: "currency", currency: project.currency || "EUR" }).format(n);
</script>

<svelte:head>
  <title>{project.name}</title>
</svelte:head>

<div class="mb-6 flex items-start justify-between">
  <div>
    <a href="/projects" class="text-sm text-neutral-500 hover:text-neutral-900">← {t("projects.title")}</a>
    <h1 class="mt-1 text-xl font-semibold text-neutral-900">{project.name}</h1>
    <p class="mt-1 text-sm text-neutral-500">
      {#if companyName}{companyName} · {/if}{t(`projects.status.${project.status}`)}
    </p>
  </div>
  <form method="POST" action="?/deleteProject" use:enhance>
    <button class="rounded-lg border border-neutral-300 px-3 py-2 text-sm text-neutral-500 hover:border-red-300 hover:text-red-600">
      {t("common.delete")}
    </button>
  </form>
</div>

<div class="grid gap-4 lg:grid-cols-2">
  <!-- Budget overview -->
  <section class="rounded-xl border border-neutral-200 bg-white p-5">
    <h2 class="mb-4 text-sm font-semibold text-neutral-900">{t("projects.budget")}</h2>
    <dl class="grid grid-cols-2 gap-4 text-sm">
      <div>
        <dt class="text-neutral-500">{t("projects.field.budget_hours")}</dt>
        <dd class="mt-0.5 font-medium text-neutral-900">
          {project.budget_hours != null ? `${project.budget_hours} ${t("projects.hours_unit")}` : "—"}
        </dd>
      </div>
      <div>
        <dt class="text-neutral-500">{t("projects.field.hourly_rate")}</dt>
        <dd class="mt-0.5 font-medium text-neutral-900">
          {project.hourly_rate != null ? money(project.hourly_rate) : "—"}
        </dd>
      </div>
      <div>
        <dt class="text-neutral-500">{t("projects.planned_value")}</dt>
        <dd class="mt-0.5 font-medium text-neutral-900">
          {plannedValue != null ? money(plannedValue) : "—"}
        </dd>
      </div>
      <div>
        <dt class="text-neutral-500">{t("projects.field.billable_default")}</dt>
        <dd class="mt-0.5 font-medium text-neutral-900">
          {project.billable_default ? t("common.yes") : t("common.no")}
        </dd>
      </div>
    </dl>
    <div class="mt-4 border-t border-neutral-100 pt-4">
      <div class="flex items-end justify-between text-sm">
        <span class="text-neutral-500">{t("projects.logged")}</span>
        <span class="font-medium text-neutral-900">
          {loggedHours} {t("projects.hours_unit")}{#if project.budget_hours != null}
            <span class="text-neutral-400"> / {project.budget_hours} {t("projects.hours_unit")}</span>
          {/if}
        </span>
      </div>
      {#if budgetPct != null}
        <div class="mt-1.5 h-2 overflow-hidden rounded-full bg-neutral-100">
          <div class="h-full rounded-full {budgetPct >= 100 ? 'bg-red-500' : 'bg-brand'}" style="width: {budgetPct}%"></div>
        </div>
      {/if}
      {#if billableValue != null}
        <div class="mt-3 flex items-center justify-between text-sm">
          <span class="text-neutral-500">{t("projects.billable_value")}</span>
          <span class="font-medium text-neutral-900">{money(billableValue)}</span>
        </div>
      {/if}
    </div>
  </section>

  <!-- Edit -->
  <section class="rounded-xl border border-neutral-200 bg-white p-5">
    <h2 class="mb-4 text-sm font-semibold text-neutral-900">{t("common.edit")}</h2>
    <form method="POST" action="?/update" use:enhance class="space-y-3">
      <input type="hidden" name="name" value={project.name} />
      <div class="grid grid-cols-2 gap-3">
        <div>
          <label for="status" class="mb-1 block text-sm font-medium text-neutral-700">{t("projects.field.status")}</label>
          <select id="status" name="status" class={inputClass}>
            {#each STATUSES as s (s)}
              <option value={s} selected={project.status === s}>{t(`projects.status.${s}`)}</option>
            {/each}
          </select>
        </div>
        <div class="flex items-center gap-2 pt-6">
          <input id="billable_default" name="billable_default" type="checkbox" checked={project.billable_default}
            class="h-4 w-4 rounded border-neutral-300 text-brand focus:ring-brand" />
          <label for="billable_default" class="text-sm font-medium text-neutral-700">{t("projects.field.billable_default")}</label>
        </div>
        <div>
          <label for="budget_hours" class="mb-1 block text-sm font-medium text-neutral-700">{t("projects.field.budget_hours")}</label>
          <input id="budget_hours" name="budget_hours" type="number" min="0" step="0.5" value={project.budget_hours ?? ""} class={inputClass} />
        </div>
        <div>
          <label for="hourly_rate" class="mb-1 block text-sm font-medium text-neutral-700">{t("projects.field.hourly_rate")}</label>
          <input id="hourly_rate" name="hourly_rate" type="number" min="0" step="0.01" value={project.hourly_rate ?? ""} class={inputClass} />
        </div>
        <div>
          <label for="budget_amount" class="mb-1 block text-sm font-medium text-neutral-700">{t("projects.field.budget_amount")}</label>
          <input id="budget_amount" name="budget_amount" type="number" min="0" step="0.01" value={project.budget_amount ?? ""} class={inputClass} />
        </div>
      </div>
      {#if form?.updated}<p class="text-sm text-green-600">{t("settings.account.saved")}</p>{/if}
      <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90">{t("common.save")}</button>
    </form>
  </section>
</div>

<!-- To-dos -->
<section class="mt-4 rounded-xl border border-neutral-200 bg-white p-5">
  <div class="mb-4 flex items-center justify-between">
    <h2 class="text-sm font-semibold text-neutral-900">{t("projects.todos")}</h2>
    <span class="text-xs text-neutral-500">{t("projects.todos_progress", { done: doneCount, total: tasks.length })}</span>
  </div>

  <form method="POST" action="?/addTask" use:enhance class="mb-4 flex gap-2">
    <input type="hidden" name="company_id" value={project.company_id ?? ""} />
    <input name="title" placeholder={t("projects.add_todo")} required class={inputClass} />
    <button class="shrink-0 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90">{t("common.create")}</button>
  </form>

  {#if tasks.length === 0}
    <p class="text-sm text-neutral-500">{t("tasks.empty")}</p>
  {:else}
    <ul class="divide-y divide-neutral-100">
      {#each tasks as task (task.id)}
        <li class="flex items-center gap-3 py-2">
          <form method="POST" action="?/toggleTask" use:enhance>
            <input type="hidden" name="id" value={task.id} />
            <input type="hidden" name="status" value={task.status === "done" ? "open" : "done"} />
            <button
              class="flex h-5 w-5 items-center justify-center rounded border text-xs
                {task.status === 'done' ? 'border-brand bg-brand text-white' : 'border-neutral-300 text-transparent'}"
              aria-label={t("tasks.toggle_done")}
            >✓</button>
          </form>
          <span class="flex-1 text-sm {task.status === 'done' ? 'text-neutral-400 line-through' : 'text-neutral-900'}">
            {task.title}
          </span>
          <form method="POST" action="?/deleteTask" use:enhance>
            <input type="hidden" name="id" value={task.id} />
            <button class="text-xs text-neutral-400 hover:text-red-600">{t("common.delete")}</button>
          </form>
        </li>
      {/each}
    </ul>
  {/if}
</section>
