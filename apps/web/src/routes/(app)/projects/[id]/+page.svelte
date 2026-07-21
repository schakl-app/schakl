<script lang="ts">
  import { Pencil, Trash2 } from "@lucide/svelte";
  import { dndzone } from "svelte-dnd-action";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import CustomFieldsForm from "$lib/core/customfields/CustomFieldsForm.svelte";
  import CustomFieldsView from "$lib/core/customfields/CustomFieldsView.svelte";
  import { burnBarClass, burnBarWidth, burnPct } from "$lib/core/burn";
  import { editIntent } from "$lib/core/edit-intent";
  import { fmtNumber, fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import { can } from "$lib/core/permissions";
  import { entityPanelsFor } from "$lib/core/registry";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import AssigneePicker from "$lib/core/ui/AssigneePicker.svelte";
  import AvatarStack from "$lib/core/ui/AvatarStack.svelte";
  import FormCheckbox from "$lib/core/ui/FormCheckbox.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import FileAttachments from "$lib/core/ui/FileAttachments.svelte";
  import Markdown from "$lib/core/ui/Markdown.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import RichTextEditor from "$lib/core/ui/RichTextEditor.svelte";
  import InteractionForm from "$lib/modules/interactions/InteractionForm.svelte";
  import { terminalKeys } from "$lib/modules/tasks/statuses";
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

  // Use mode vs edit mode (UX §3): the definition is edited behind the ⋯ → Bewerken toggle, or
  // opened straight into edit when reached from the overview's ⋯ → Bewerken (#78).
  let editing = $state(editIntent());
  let confirmDelete = $state(false);

  // Log a contactmoment from the header — quick-add where the user is (docs/UX.md), with the
  // project (and its client) pinned. Renders only when the interactions module is enabled.
  let showLogInteraction = $state(false);
  const canLogInteraction = $derived(
    enabled.includes("interactions") && can(page.data.user, "interactions.interaction.write"),
  );
  const mentionCandidates = $derived(
    data.members.map((m) => ({ id: m.user_id, name: m.full_name || m.email })),
  );

  const STATUSES = ["active", "on_hold", "completed", "archived"] as const;
  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";

  const project = $derived(data.project);
  const tasks = $derived(data.tasks);
  // "Done" is any terminal configured status (issue #62), not the literal "done".
  const terminalSet = $derived(new Set(terminalKeys(data.statuses)));
  const doneCount = $derived(tasks.filter((t) => terminalSet.has(t.status)).length);

  const assignees = $derived(project.assignees ?? []);

  // Hours sourced from covering subscriptions (#225): non-empty locks the budget fields and
  // the effective figure is the API's derived one, not the stored (dormant) budget_hours.
  const budgetSources = $derived(project.budget_sources ?? []);
  const subscriptionBacked = $derived(budgetSources.length > 0);
  const budgetSourceNames = $derived(budgetSources.map((s) => s.name).join(", "));
  const budgetHours = $derived(project.hours?.budget_hours ?? project.budget_hours ?? null);

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
  // Actuals from logged time (team-wide) against the budget, computed by the API over the budget's
  // current period — the same figures the projects list column shows, and the same period the Uren
  // panel below lists the entries for. Money is priced per logger (#226): the API's employee-rate
  // query carries the billable value and the cost, loaded only for rate-money readers.
  const loggedHours = $derived(project.hours?.spent_hours ?? 0);
  // The one burn scale (core/burn.ts, docs/UX.md). Unclamped: this used to `Math.min(100, …)`,
  // so a project 40 % over budget drew exactly like one that had just landed on it.
  const budgetPct = $derived(burnPct(loggedHours, budgetHours));

  const money = (n: number) =>
    new Intl.NumberFormat("nl-NL", {
      style: "currency",
      currency: project.currency || "EUR",
    }).format(n);
</script>

<svelte:head>
  <title>{pageTitle(project.name)}</title>
</svelte:head>

<div class="mb-6 flex items-start justify-between">
  <div>
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
  <div class="flex flex-wrap items-center gap-2">
    {#if canLogInteraction}
      <button
        type="button"
        onclick={() => (showLogInteraction = true)}
        class="rounded-lg border border-border px-3 py-1.5 text-sm text-text-muted hover:border-brand hover:text-brand"
      >
        {t("interactions.add")}
      </button>
    {/if}
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
</div>

<div class="grid gap-4 lg:grid-cols-2">
  <!-- Budget overview -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="mb-4 text-sm font-semibold text-text">{t("projects.budget")}</h2>
    <dl class="grid grid-cols-2 gap-4 text-sm">
      <div>
        <dt class="text-text-muted">{t("projects.field.budget_hours")}</dt>
        <dd class="mt-0.5 font-medium text-text">
          {budgetHours != null ? `${budgetHours} ${t("projects.hours_unit")}` : "—"}
          {#if subscriptionBacked}
            <!-- At-a-glance connection state (#225): the hours are subscription-backed. -->
            <a
              href="/subscriptions"
              title={budgetSourceNames}
              class="ml-1 inline-block rounded-md bg-surface px-2 py-0.5 text-xs font-medium text-text-muted hover:text-brand"
            >
              {t("projects.hours_from_subscription")}
            </a>
          {/if}
        </dd>
      </div>
      <div>
        <dt class="text-text-muted">{t("projects.field.budget_amount")}</dt>
        <dd class="mt-0.5 font-medium text-text">
          {project.budget_amount != null ? money(project.budget_amount) : "—"}
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
        <!-- The API's effective period: forced to monthly when a subscription sources the
             hours (#225), the project's own otherwise. -->
        <span class="text-text-muted"
          >{t(
            `projects.logged_period.${project.hours?.period ?? project.budget_period ?? "total"}`,
          )}</span
        >
        <span class="font-medium text-text">
          {loggedHours}
          {t("projects.hours_unit")}{#if budgetHours != null}
            <span class="text-text-muted"> / {budgetHours} {t("projects.hours_unit")}</span>
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
      <!-- Money from employee rates (#111, #226): every hour is priced at its logger's rate,
           so billable value and cost come from the same API query. Only loaded (and rendered)
           for someone the API lets read rate-derived money. -->
      {#if data.cost}
        <div class="mt-3 flex items-center justify-between text-sm">
          <span class="text-text-muted">{t("projects.billable_value")}</span>
          <span class="font-medium text-text">{money(data.cost.billable_amount)}</span>
        </div>
        <div class="mt-2 flex items-center justify-between text-sm">
          <span class="text-text-muted">{t("projects.cost")}</span>
          <span class="font-medium text-text">{money(data.cost.cost)}</span>
        </div>
        {#if data.cost.unrated_minutes > 0}
          <p class="mt-1 text-xs text-text-muted">
            {t("projects.cost_unrated", {
              hours: fmtNumber(data.cost.unrated_minutes / 60, 1),
            })}
          </p>
        {/if}
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
            <FormCheckbox
              id="billable_default"
              name="billable_default"
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
            <!-- Subscription-backed hours are read-only here (#225): a disabled input never
                 posts, so the action omits the field and the API guard never trips. -->
            <input
              id="budget_hours"
              name="budget_hours"
              type="number"
              min="0"
              step="0.5"
              value={subscriptionBacked ? (budgetHours ?? "") : (project.budget_hours ?? "")}
              disabled={subscriptionBacked}
              class="{inputClass} disabled:bg-surface disabled:text-text-muted"
            />
            {#if subscriptionBacked}
              <p class="mt-1 text-xs text-text-muted">
                {t("projects.hours_from_subscription_hint")}
                <a href="/subscriptions" class="text-brand hover:underline">{budgetSourceNames}</a>
              </p>
            {/if}
          </div>
          <div>
            <label for="budget_period" class="mb-1 block text-sm font-medium text-text"
              >{t("projects.field.budget_period")}</label
            >
            <select
              id="budget_period"
              name="budget_period"
              disabled={subscriptionBacked}
              class="{inputClass} disabled:bg-surface disabled:text-text-muted"
            >
              {#each ["total", "monthly", "weekly", "daily"] as period (period)}
                <option
                  value={period}
                  selected={(subscriptionBacked
                    ? "monthly"
                    : (project.budget_period ?? "total")) === period}
                >
                  {t(`projects.budget_period.${period}`)}
                </option>
              {/each}
            </select>
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
          <RichTextEditor
            id="edit-description"
            name="description"
            rows={3}
            value={project.description ?? ""}
          />
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
            <dd class="mt-0.5">
              <Markdown value={project.description} />
            </dd>
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
{#snippet panelSections(specs: typeof data.panels)}
  {#each specs as panel (panel.key)}
    {@const PanelComponent = panelComponent(panel.key)}
    {#if PanelComponent}
      <section class="mt-4 rounded-xl border border-border bg-surface-raised p-5">
        <h2 class="mb-3 text-sm font-semibold text-text">{t(panel.titleKey)}</h2>
        <PanelComponent data={panel.data} context={data.context} lookups={panelLookups} />
      </section>
    {/if}
  {/each}
{/snippet}

{@render panelSections(data.panels.filter((panel) => panel.position < 90))}

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
            <TaskRow
              {task}
              toggleAction="?/toggleTask"
              members={data.members}
              statuses={data.statuses}
            />
          </div>
        </div>
      {/each}
    </div>
  {/if}
</section>

<!-- Documents attached through the storage core (#123). -->
<section class="mt-4 rounded-xl border border-border bg-surface-raised p-5">
  <h2 class="mb-3 text-sm font-semibold text-text">{t("files.title")}</h2>
  {#if data.files.length === 0}
    <p class="mb-3 text-sm text-text-muted">{t("files.empty")}</p>
  {/if}
  <FileAttachments
    files={data.files}
    uploadAction="?/uploadFile"
    deleteAction="?/deleteFile"
    error={form?.fileError ?? null}
  />
</section>

<!-- The activity trail hangs last — history sits under the working surfaces, never between
     them and the to-dos (docs/UX.md principle 4). -->
{@render panelSections(data.panels.filter((panel) => panel.position >= 90))}

<Modal bind:open={showLogInteraction} title={t("interactions.add")}>
  <InteractionForm
    prefill={{ project_id: project.id, company_id: project.company_id ?? null }}
    mentions={mentionCandidates}
    onsaved={() => (showLogInteraction = false)}
  />
</Modal>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("common.delete")}
  message={t("projects.delete_confirm", { name: project.name })}
  action="?/deleteProject"
/>
