<script lang="ts">
  /**
   * Tasks → E-mailinbox (`/tasks/inbox`): the caller's own mails to the task address.
   *
   * Not a permanent tab (the Timeon lesson, CLAUDE.md §10): the E-mailinbox tab is drawn with a
   * count only while a mail is waiting, or while you are here. A parked mail keeps everything
   * it carried — the words, the attachments, what the parser and the model made of it — and
   * `Taak aanmaken` opens the ordinary quick-create dialog over it, prefilled, posting to the
   * mail's own create action so the rest travels with the task.
   */
  import { Mail, Paperclip } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { fmtRelativeTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import Button from "$lib/core/ui/Button.svelte";
  import Markdown from "$lib/core/ui/Markdown.svelte";
  import PageHeader from "$lib/core/ui/PageHeader.svelte";
  import TaskQuickCreate from "$lib/modules/tasks/TaskQuickCreate.svelte";
  import TasksNav from "$lib/modules/tasks/TasksNav.svelte";

  let { data, form } = $props();

  type Row = (typeof data.items)[number];

  const waiting = $derived(
    data.items.filter((row) => row.status === "needs_client" || row.status === "refused"),
  );
  const recent = $derived(
    data.items.filter((row) => row.status !== "needs_client" && row.status !== "refused"),
  );

  // The quick-create dialog, opened over one parked mail: its title and roster prefilled from
  // what the parser and the model already decided, the client left to the picker — the one
  // thing the mail could not say is the one thing this screen exists to ask.
  let creating = $state<Row | null>(null);
  let quickCreateOpen = $state(false);
  function startCreate(row: Row) {
    creating = row;
    quickCreateOpen = true;
  }
  const hints = $derived((creating?.hints ?? {}) as Record<string, unknown>);
  const prefilledAssignee = $derived(
    typeof hints.assignee_user_id === "string" && hints.assignee_user_id
      ? hints.assignee_user_id
      : (page.data.user?.id ?? null),
  );

  const busy = new InFlight();

  function reasonLabel(row: Row): string {
    return row.reason ? t(`tasks.inbox.reason.${row.reason}`) : "";
  }
  function byModel(row: Row): string {
    const fields = ((row.hints as Record<string, unknown> | null)?.by_model ?? []) as string[];
    return fields.length ? t("tasks.inbox.by_model", { fields: fields.join(", ") }) : "";
  }
</script>

<svelte:head>
  <title>{pageTitle(t("tasks.inbox.title"))}</title>
</svelte:head>

<TasksNav />

<PageHeader title={t("tasks.inbox.title")}>
  {#snippet subtitle()}
    {t("tasks.inbox.subtitle")}
    {#if data.intakeAddress}
      <span class="ml-1 font-medium text-text">
        {t("tasks.inbox.address", { address: data.intakeAddress })}
      </span>
    {/if}
  {/snippet}
</PageHeader>

{#if form?.error}
  <p class="mb-4 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
{/if}

{#if data.items.length === 0}
  <div
    class="rounded-xl border border-dashed border-border p-8 text-center text-sm text-text-muted"
  >
    <Mail class="mx-auto mb-2 opacity-60" size={20} />
    {t("tasks.inbox.empty")}
  </div>
{/if}

{#if waiting.length > 0}
  <h2 class="mb-2 text-sm font-semibold text-text">{t("tasks.inbox.waiting")}</h2>
  <div class="mb-8 space-y-3">
    {#each waiting as row (row.id)}
      {@const files = data.filesByIntake[row.id] ?? []}
      <article
        id="intake-{row.id}"
        data-intake-id={row.id}
        class="rounded-xl border bg-surface-raised p-4 {data.open === row.id
          ? 'border-brand ring-1 ring-brand'
          : 'border-border'}"
      >
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0">
            <h3 class="truncate text-base font-semibold text-text">
              {row.subject || t("tasks.intake.untitled")}
            </h3>
            <p class="text-xs text-text-muted">
              {t("tasks.inbox.received", { when: fmtRelativeTime(row.received_at) })}
              · <span class="text-amber-700 dark:text-amber-400">{reasonLabel(row)}</span>
            </p>
          </div>
          <div class="flex items-center gap-2">
            <form method="POST" action="?/discard" use:enhance={busy.clear()}>
              <input type="hidden" name="id" value={row.id} />
              <Button type="submit" variant="danger-outline" loading={busy.active}>
                {t("tasks.inbox.discard")}
              </Button>
            </form>
            {#if row.status === "needs_client"}
              <Button type="button" onclick={() => startCreate(row)}>
                {t("tasks.inbox.create")}
              </Button>
            {/if}
          </div>
        </div>
        {#if row.body_markdown || row.body_text}
          <div
            class="mt-3 max-h-64 overflow-y-auto rounded-lg border border-border bg-surface p-3 text-sm"
          >
            <Markdown value={row.body_markdown ?? row.body_text} images />
          </div>
        {/if}
        {#if byModel(row)}
          <p class="mt-2 text-xs text-text-muted">{byModel(row)}</p>
        {/if}
        {#if files.length > 0}
          <p class="mt-2 flex flex-wrap items-center gap-2 text-xs text-text-muted">
            <Paperclip size={12} />
            <span class="font-medium">{t("tasks.inbox.attachments")}:</span>
            {#each files as file (file.id)}
              <a href="/api/v1/files/{file.id}" class="text-brand hover:underline">
                {file.filename}
              </a>
            {/each}
          </p>
        {/if}
      </article>
    {/each}
  </div>
{/if}

{#if recent.length > 0}
  <h2 class="mb-2 text-sm font-semibold text-text">{t("tasks.inbox.recent")}</h2>
  <ul class="divide-y divide-border rounded-xl border border-border bg-surface-raised">
    {#each recent as row (row.id)}
      <li class="flex flex-wrap items-center justify-between gap-2 px-4 py-2 text-sm">
        <div class="min-w-0">
          <span class="font-medium text-text">{row.subject || t("tasks.intake.untitled")}</span>
          <span class="ml-2 text-xs text-text-muted">
            {t("tasks.inbox.received", { when: fmtRelativeTime(row.received_at) })} ·
            {t(`tasks.inbox.status.${row.status}`)}
          </span>
        </div>
        {#if row.task_id}
          <a href="/tasks/{row.task_id}" class="text-xs text-brand hover:underline">
            {t("tasks.inbox.open_task")}
          </a>
        {/if}
      </li>
    {/each}
  </ul>
{/if}

{#if creating}
  <TaskQuickCreate
    bind:open={quickCreateOpen}
    title={String(hints.title ?? creating.subject ?? "")}
    assignees={prefilledAssignee ? [{ user_id: prefilledAssignee, is_primary: true }] : []}
    action="?/createFromIntake&id={creating.id}"
    error={form?.qcError ?? null}
    pickerSlot="task_intake"
  />
{/if}
