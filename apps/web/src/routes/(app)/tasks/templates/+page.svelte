<script lang="ts">
  import { Pencil, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import { can } from "$lib/core/permissions";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import TasksNav from "$lib/modules/tasks/TasksNav.svelte";

  let { data, form } = $props();

  const canManageTemplates = $derived(can(page.data.user, "tasks.template.write"));

  // --- checklist template repository state ------------------------------------
  let editingChecklistId = $state<string | null>(null);
  let deleteChecklistId = $state("");
  let confirmDeleteChecklist = $state(false);

  const COMPANY_STATUSES = ["lead", "onboarding", "active", "offboarding", "archived"] as const;
  const priorities = ["low", "normal", "high"] as const;

  interface ItemDraft {
    title: string;
    description: string;
    priority: string;
    relative_due_days: string;
    allocated_minutes: string;
    assignee_user_id: string;
    requires_interaction: boolean;
    checklist_title: string;
    checklist_text: string;
  }

  interface TemplateLike {
    id?: string;
    name: string;
    trigger: string;
    trigger_status?: string | null;
    active: boolean;
    items?: {
      title: string;
      description?: string | null;
      priority?: string;
      relative_due_days?: number | null;
      assignee_user_id?: string | null;
      assign_responsible?: boolean;
      requires_interaction?: boolean;
      checklist_title?: string | null;
      checklist_items?: { title: string; description?: string | null }[];
    }[];
  }

  let editing = $state<TemplateLike | null>(null);
  let items = $state<ItemDraft[]>([]);
  let trigger = $state("manual");
  let deleteId = $state("");
  let confirmDelete = $state(false);

  function blankItem(): ItemDraft {
    return {
      title: "",
      description: "",
      priority: "normal",
      relative_due_days: "",
      allocated_minutes: "",
      assignee_user_id: "",
      requires_interaction: false,
      checklist_title: "",
      checklist_text: "",
    };
  }

  function startNew() {
    editing = { name: "", trigger: "manual", trigger_status: null, active: true, items: [] };
    trigger = "manual";
    items = [blankItem()];
  }

  function startEdit(template: (typeof data.templates)[number]) {
    editing = template;
    trigger = template.trigger;
    items = (template.items ?? []).map((item) => ({
      title: item.title,
      description: item.description ?? "",
      priority: item.priority ?? "normal",
      relative_due_days: item.relative_due_days == null ? "" : String(item.relative_due_days),
      allocated_minutes: item.allocated_minutes == null ? "" : String(item.allocated_minutes),
      // "__responsible__" is the apply-time sentinel (#28), never a real user id.
      assignee_user_id: item.assign_responsible ? "__responsible__" : (item.assignee_user_id ?? ""),
      requires_interaction: item.requires_interaction ?? false,
      checklist_title: item.checklist_title ?? "",
      checklist_text: (item.checklist_items ?? []).map((c) => c.title).join("\n"),
    }));
    if (items.length === 0) items = [blankItem()];
  }

  function move(index: number, delta: number) {
    const next = index + delta;
    if (next < 0 || next >= items.length) return;
    const copy = [...items];
    [copy[index], copy[next]] = [copy[next], copy[index]];
    items = copy;
  }

  const itemsJson = $derived(
    JSON.stringify(
      items.map((item) => ({
        title: item.title,
        description: item.description,
        priority: item.priority,
        relative_due_days: item.relative_due_days === "" ? null : Number(item.relative_due_days),
        allocated_minutes: item.allocated_minutes === "" ? null : Number(item.allocated_minutes),
        assignee_user_id:
          item.assignee_user_id && item.assignee_user_id !== "__responsible__"
            ? item.assignee_user_id
            : null,
        assign_responsible: item.assignee_user_id === "__responsible__",
        requires_interaction: item.requires_interaction,
        checklist_title: item.checklist_title,
        checklist_items: item.checklist_text
          .split("\n")
          .map((s) => s.trim())
          .filter(Boolean),
      })),
    ),
  );

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<svelte:head>
  <title>{pageTitle(t("settings.task_templates.title"))}</title>
</svelte:head>

<TasksNav />

<div class="mb-6 flex items-start justify-between">
  <div>
    <h1 class="text-xl font-semibold text-text">{t("settings.task_templates.title")}</h1>
    <p class="mt-1 text-sm text-text-muted">{t("settings.task_templates.subtitle")}</p>
  </div>
  {#if canManageTemplates}
    <button
      class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
      onclick={startNew}
    >
      {t("tasks.templates.new")}
    </button>
  {/if}
</div>

{#if editing}
  <form
    method="POST"
    action={editing.id ? "?/update" : "?/create"}
    use:enhance={() =>
      ({ update }) => {
        editing = null;
        void update();
      }}
    class="mb-6 rounded-xl border border-border bg-surface-raised p-5"
  >
    {#if editing.id}<input type="hidden" name="id" value={editing.id} />{/if}
    <input type="hidden" name="items_json" value={itemsJson} />

    <div class="grid gap-3 sm:grid-cols-2">
      <div>
        <label for="tpl-name" class="mb-1 block text-sm font-medium text-text"
          >{t("tasks.templates.name")}</label
        >
        <input id="tpl-name" name="name" value={editing.name} required class={inputClass} />
      </div>
      <div class="flex items-end gap-3">
        <div class="flex-1">
          <label for="tpl-trigger" class="mb-1 block text-sm font-medium text-text"
            >{t("tasks.templates.trigger")}</label
          >
          <select id="tpl-trigger" name="trigger" bind:value={trigger} class={inputClass}>
            <option value="manual">{t("tasks.templates.trigger_manual")}</option>
            <option value="company_status">{t("tasks.templates.trigger_company_status")}</option>
          </select>
        </div>
        {#if trigger === "company_status"}
          <div class="flex-1">
            <label for="tpl-trigger-status" class="mb-1 block text-sm font-medium text-text"
              >{t("tasks.templates.trigger_status")}</label
            >
            <select id="tpl-trigger-status" name="trigger_status" class={inputClass}>
              {#each COMPANY_STATUSES as status (status)}
                <option
                  value={status}
                  selected={editing.trigger_status === status ||
                    (!editing.trigger_status && status === "onboarding")}
                >
                  {t(`companies.status.${status}`)}
                </option>
              {/each}
            </select>
          </div>
        {/if}
        <label class="flex items-center gap-2 pb-2 text-sm text-text">
          <input
            type="checkbox"
            name="active"
            checked={editing.active}
            class="h-4 w-4 rounded border-border text-brand focus:ring-brand"
          />
          {t("tasks.templates.active")}
        </label>
      </div>
    </div>

    <h3 class="mt-5 mb-2 text-xs font-semibold uppercase tracking-wide text-text-muted">
      {t("tasks.templates.items")}
    </h3>
    <div class="space-y-3">
      {#each items as item, i (i)}
        <div class="rounded-lg border border-border p-3">
          <div class="grid gap-2 sm:grid-cols-[1fr_auto_auto_auto_auto]">
            <input
              placeholder={t("tasks.field.title")}
              bind:value={item.title}
              class={inputClass}
            />
            <select
              bind:value={item.priority}
              class="rounded-lg border border-border px-2 py-2 text-sm"
              aria-label={t("tasks.field.priority")}
            >
              {#each priorities as p (p)}<option value={p}>{t(`tasks.priority.${p}`)}</option
                >{/each}
            </select>
            <div class="flex items-center gap-1 text-sm text-text-muted">
              <input
                type="number"
                min="0"
                max="365"
                bind:value={item.relative_due_days}
                placeholder="—"
                class="w-16 rounded-lg border border-border px-2 py-2 text-sm"
                aria-label={t("tasks.templates.relative_due_days")}
              />
              <span class="text-xs">{t("tasks.templates.days")}</span>
            </div>
            <div class="flex items-center gap-1 text-sm text-text-muted">
              <input
                type="number"
                min="0"
                step="15"
                bind:value={item.allocated_minutes}
                placeholder="—"
                class="w-20 rounded-lg border border-border px-2 py-2 text-sm"
                aria-label={t("tasks.field.allocated_input")}
              />
              <span class="text-xs">{t("tasks.templates.minutes")}</span>
            </div>
            <div class="flex items-center gap-1">
              <button
                type="button"
                class="rounded border border-border px-1.5 py-1 text-xs text-text-muted hover:border-brand"
                onclick={() => move(i, -1)}
                aria-label="↑">↑</button
              >
              <button
                type="button"
                class="rounded border border-border px-1.5 py-1 text-xs text-text-muted hover:border-brand"
                onclick={() => move(i, 1)}
                aria-label="↓">↓</button
              >
              <button
                type="button"
                class="rounded border border-border px-1.5 py-1 text-xs text-text-muted hover:border-red-300 hover:text-red-600 dark:hover:border-red-800 dark:hover:text-red-400"
                onclick={() => (items = items.filter((_, j) => j !== i))}
                aria-label={t("common.delete")}>✕</button
              >
            </div>
          </div>
          <div class="mt-2 grid gap-2 sm:grid-cols-2">
            <textarea
              placeholder={t("tasks.field.description")}
              bind:value={item.description}
              rows="1"
              class={inputClass}></textarea>
            <select
              bind:value={item.assignee_user_id}
              class={inputClass}
              aria-label={t("tasks.field.assignee")}
            >
              <option value="">{t("tasks.templates.no_assignee")}</option>
              <!-- Resolved at apply time to the company's primary responsible (#28). -->
              <option value="__responsible__">{t("tasks.templates.assignee_responsible")}</option>
              {#each data.members as member (member.user_id)}
                <option value={member.user_id}>{member.full_name || member.email}</option>
              {/each}
            </select>
            <input
              placeholder={t("tasks.templates.checklist_title")}
              bind:value={item.checklist_title}
              class={inputClass}
            />
            <textarea
              placeholder={t("tasks.templates.checklist_items_hint")}
              bind:value={item.checklist_text}
              rows="2"
              class={inputClass}></textarea>
          </div>
          <label class="mt-2 flex items-start gap-2 text-sm text-text">
            <input type="checkbox" bind:checked={item.requires_interaction} class="mt-0.5 shrink-0" />
            <span>
              <span class="font-medium">{t("tasks.field.requires_interaction")}</span>
              <span class="mt-0.5 block text-[11px] leading-snug text-text-muted"
                >{t("tasks.templates.requires_interaction_hint")}</span
              >
            </span>
          </label>
        </div>
      {/each}
    </div>
    <button
      type="button"
      class="mt-3 rounded-lg border border-dashed border-border px-3 py-1.5 text-sm text-text-muted hover:border-brand hover:text-brand"
      onclick={() => (items = [...items, blankItem()])}
    >
      ＋ {t("tasks.templates.add_item")}
    </button>

    {#if form?.error}<p class="mt-2 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>{/if}
    <div class="mt-4 flex gap-2">
      <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >{t("common.save")}</button
      >
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm"
        onclick={() => (editing = null)}>{t("common.cancel")}</button
      >
    </div>
  </form>
{/if}

{#if data.templates.length === 0 && !editing}
  <div class="rounded-xl border border-dashed border-border bg-surface-raised p-10 text-center">
    <p class="font-medium text-text">{t("tasks.templates.empty")}</p>
    <p class="mt-1 text-sm text-text-muted">{t("tasks.templates.empty_hint")}</p>
  </div>
{:else}
  <ul class="space-y-3">
    {#each data.templates as template (template.id)}
      <li class="rounded-xl border border-border bg-surface-raised p-4">
        <div class="flex items-center justify-between gap-3">
          <div>
            <div class="flex items-center gap-2">
              <h3 class="text-sm font-semibold text-text">{template.name}</h3>
              {#if !template.active}
                <span class="rounded-full bg-surface px-2 py-0.5 text-[11px] text-text-muted"
                  >{t("tasks.templates.inactive")}</span
                >
              {/if}
            </div>
            <p class="mt-0.5 text-xs text-text-muted">
              {#if template.trigger === "company_status" && template.trigger_status}
                {t("tasks.templates.auto_hint", {
                  status: t(`companies.status.${template.trigger_status}`),
                })}
              {:else}
                {t("tasks.templates.trigger_manual")}
              {/if}
              · {t("tasks.templates.item_count", { count: (template.items ?? []).length })}
            </p>
          </div>
          {#if canManageTemplates}
            <ActionsMenu
              items={[
                { label: t("common.edit"), icon: Pencil, onclick: () => startEdit(template) },
                {
                  label: t("common.delete"),
                  icon: Trash2,
                  danger: true,
                  onclick: () => {
                    deleteId = template.id;
                    confirmDelete = true;
                  },
                },
              ]}
            />
          {/if}
        </div>
        {#if (template.items ?? []).length > 0}
          <ul class="mt-2 flex flex-wrap gap-1.5">
            {#each template.items ?? [] as item (item.id)}
              <li class="rounded-full bg-surface px-2 py-0.5 text-[11px] text-text-muted">
                {item.title}
              </li>
            {/each}
          </ul>
        {/if}
      </li>
    {/each}
  </ul>
{/if}

<!-- Checklist template repository (shared per instance, staff-editable) -->
<section class="mt-8">
  <div class="mb-3">
    <h2 class="text-base font-semibold text-text">{t("tasks.checklist_templates.title")}</h2>
    <p class="mt-0.5 text-sm text-text-muted">{t("tasks.checklist_templates.subtitle")}</p>
  </div>

  <div class="grid gap-4 lg:grid-cols-[1fr_320px]">
    <div class="space-y-3">
      {#if data.checklistTemplates.length === 0}
        <div
          class="rounded-xl border border-dashed border-border bg-surface-raised p-8 text-center"
        >
          <p class="text-sm text-text-muted">{t("tasks.checklist_templates.empty")}</p>
        </div>
      {:else}
        {#each data.checklistTemplates as checklistTemplate (checklistTemplate.id)}
          <div class="rounded-xl border border-border bg-surface-raised p-4">
            {#if editingChecklistId === checklistTemplate.id}
              <form
                method="POST"
                action="?/updateChecklist"
                use:enhance={() =>
                  ({ update }) => {
                    editingChecklistId = null;
                    void update();
                  }}
                class="space-y-2"
              >
                <input type="hidden" name="id" value={checklistTemplate.id} />
                <input name="title" value={checklistTemplate.title} required class={inputClass} />
                <textarea
                  name="items"
                  rows="4"
                  class={inputClass}
                  placeholder={t("tasks.templates.checklist_items_hint")}
                  >{(checklistTemplate.items ?? []).join("\n")}</textarea
                >
                <div class="flex gap-2">
                  <button class="rounded-lg bg-brand px-3 py-1.5 text-xs font-medium text-white"
                    >{t("common.save")}</button
                  >
                  <button
                    type="button"
                    class="rounded-lg border border-border px-3 py-1.5 text-xs"
                    onclick={() => (editingChecklistId = null)}>{t("common.cancel")}</button
                  >
                </div>
              </form>
            {:else}
              <div class="flex items-center justify-between gap-3">
                <h3 class="text-sm font-semibold text-text">{checklistTemplate.title}</h3>
                <ActionsMenu
                  items={[
                    {
                      label: t("common.edit"),
                      icon: Pencil,
                      onclick: () => (editingChecklistId = checklistTemplate.id),
                    },
                    {
                      label: t("common.delete"),
                      icon: Trash2,
                      danger: true,
                      onclick: () => {
                        deleteChecklistId = checklistTemplate.id;
                        confirmDeleteChecklist = true;
                      },
                    },
                  ]}
                />
              </div>
              <ul class="mt-2 flex flex-wrap gap-1.5">
                {#each checklistTemplate.items ?? [] as item, i (i)}
                  <li class="rounded-full bg-surface px-2 py-0.5 text-[11px] text-text-muted">
                    {item}
                  </li>
                {/each}
              </ul>
            {/if}
          </div>
        {/each}
      {/if}
    </div>

    <aside class="h-fit rounded-xl border border-border bg-surface-raised p-5">
      <h3 class="mb-3 text-sm font-semibold text-text">
        {t("tasks.checklist_templates.new")}
      </h3>
      <form
        method="POST"
        action="?/createChecklist"
        use:enhance={() =>
          ({ update }) =>
            void update({ reset: true })}
        class="space-y-3"
      >
        <input
          name="title"
          required
          placeholder={t("tasks.templates.checklist_title")}
          class={inputClass}
        />
        <textarea
          name="items"
          rows="4"
          required
          class={inputClass}
          placeholder={t("tasks.templates.checklist_items_hint")}></textarea>
        <button
          class="w-full rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >
          {t("common.create")}
        </button>
      </form>
    </aside>
  </div>
</section>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("tasks.templates.delete")}
  message={t("tasks.templates.delete_confirm")}
  action="?/delete"
  fields={{ id: deleteId }}
/>

<ConfirmDialog
  bind:open={confirmDeleteChecklist}
  title={t("tasks.checklist_templates.delete")}
  message={t("tasks.checklist_templates.delete_confirm")}
  action="?/deleteChecklist"
  fields={{ id: deleteChecklistId }}
/>
