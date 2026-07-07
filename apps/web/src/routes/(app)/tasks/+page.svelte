<script lang="ts">
  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";

  let { data, form } = $props();

  let showCreate = $state(false);
  const userId = $derived(page.data.user?.id ?? "");

  const priorities = ["low", "normal", "high"] as const;
</script>

<svelte:head>
  <title>{t("tasks.title")}</title>
</svelte:head>

<div class="mb-6 flex items-center justify-between">
  <div>
    <h1 class="text-xl font-semibold text-neutral-900">{t("tasks.title")}</h1>
    <p class="mt-1 text-sm text-neutral-500">{t("tasks.count", { count: data.total })}</p>
  </div>
  <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
    onclick={() => (showCreate = !showCreate)}>
    {t("tasks.new")}
  </button>
</div>

{#if showCreate}
  <form method="POST" action="?/create"
    use:enhance={() => ({ update }) => { void update().then(() => (showCreate = false)); }}
    class="mb-6 rounded-xl border border-neutral-200 bg-white p-4">
    <div class="grid gap-3 sm:grid-cols-2">
      <div class="sm:col-span-2">
        <label for="title" class="mb-1 block text-sm font-medium text-neutral-700">{t("tasks.field.title")}</label>
        <input id="title" name="title" required
          class="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand" />
      </div>
      <div>
        <label for="priority" class="mb-1 block text-sm font-medium text-neutral-700">{t("tasks.field.priority")}</label>
        <select id="priority" name="priority" class="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm">
          {#each priorities as p (p)}
            <option value={p} selected={p === "normal"}>{t(`tasks.priority.${p}`)}</option>
          {/each}
        </select>
      </div>
      <div>
        <label for="due_date" class="mb-1 block text-sm font-medium text-neutral-700">{t("tasks.field.due_date")}</label>
        <input id="due_date" name="due_date" type="date"
          class="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm" />
      </div>
      <div>
        <label for="company_id" class="mb-1 block text-sm font-medium text-neutral-700">{t("tasks.field.company")}</label>
        <select id="company_id" name="company_id" class="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm">
          <option value="">{t("common.none")}</option>
          {#each data.companies as company (company.id)}
            <option value={company.id}>{company.name}</option>
          {/each}
        </select>
      </div>
      <div class="flex items-end">
        <label class="flex items-center gap-2 text-sm text-neutral-700">
          <input type="checkbox" name="assignee_user_id" value={userId} class="h-4 w-4 rounded border-neutral-300" />
          {t("tasks.assign_to_me")}
        </label>
      </div>
    </div>
    {#if form?.error}<p class="mt-2 text-sm text-red-600">{t(form.error)}</p>{/if}
    <div class="mt-4 flex gap-2">
      <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90">{t("common.save")}</button>
      <button type="button" class="rounded-lg border border-neutral-300 px-4 py-2 text-sm" onclick={() => (showCreate = false)}>{t("common.cancel")}</button>
    </div>
  </form>
{/if}

{#if data.tasks.length === 0}
  <div class="rounded-xl border border-dashed border-neutral-300 bg-white p-10 text-center">
    <p class="font-medium text-neutral-900">{t("tasks.empty")}</p>
    <p class="mt-1 text-sm text-neutral-500">{t("tasks.empty_hint")}</p>
  </div>
{:else}
  <ul class="divide-y divide-neutral-200 overflow-hidden rounded-xl border border-neutral-200 bg-white">
    {#each data.tasks as task (task.id)}
      <li class="flex items-center gap-3 px-4 py-3 hover:bg-neutral-50">
        <form method="POST" action="?/toggle" use:enhance>
          <input type="hidden" name="id" value={task.id} />
          <input type="hidden" name="status" value={task.status === "done" ? "open" : "done"} />
          <button
            class="flex h-5 w-5 items-center justify-center rounded border border-neutral-300 text-xs text-white"
            class:bg-brand={task.status === "done"}
            aria-label={t("tasks.toggle_done")}
          >
            {task.status === "done" ? "✓" : ""}
          </button>
        </form>
        <div class="min-w-0 flex-1">
          <span class="font-medium text-neutral-900" class:line-through={task.status === "done"}
            class:text-neutral-400={task.status === "done"}>{task.title}</span>
          <span class="ml-2 text-xs text-neutral-500">{t(`tasks.priority.${task.priority}`)}</span>
        </div>
        {#if task.due_date}<span class="text-xs text-neutral-500">{task.due_date}</span>{/if}
        <form method="POST" action="?/delete" use:enhance>
          <input type="hidden" name="id" value={task.id} />
          <button class="text-sm text-neutral-400 hover:text-red-600" aria-label={t("common.delete")}>{t("common.delete")}</button>
        </form>
      </li>
    {/each}
  </ul>
{/if}
