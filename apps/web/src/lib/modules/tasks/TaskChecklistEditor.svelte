<script lang="ts">
  /**
   * A task's checklists, edited in place over the API — the review slide-over's copy.
   *
   * The card edits its checklists through the page's own actions (`?/addItem`, `?/editItem`…),
   * which is right for a page *about* one task. The review slide-over is drawn on four other
   * pages (the inbox, a client, a contact, a project), none of which owns the task in its URL,
   * so this component talks to the task's own endpoints directly — the same way the slide-over
   * already fetches the row it reviews — and tells its host through `onchange` that the row it
   * holds is stale.
   *
   * What it offers is what reviewing a plan needs: tick, add a step, **click a step to edit
   * it** (Enter saves, Escape cancels, blur saves), remove one, start a new list, and let
   * schakl write the steps from the notes. Reordering and per-step descriptions stay on the
   * card — a review checks the plan against the e-mail beside it; restructuring is what the
   * card is for.
   *
   * Every write is a task write (`tasks.task.write`, `:own` means assignee), which the host
   * has already mirrored by drawing this at all; a refusal is shown, never swallowed.
   */
  import { Sparkles, Trash2 } from "@lucide/svelte";
  import { tick } from "svelte";

  import { t } from "$lib/core/i18n";
  import Button from "$lib/core/ui/Button.svelte";
  import Markdown from "$lib/core/ui/Markdown.svelte";

  interface ChecklistItem {
    id: string;
    title: string;
    description?: string | null;
    done: boolean;
  }
  interface Checklist {
    id: string;
    title: string;
    description?: string | null;
    items?: ChecklistItem[];
  }

  let {
    taskId,
    checklists = [],
    onchange,
    aiAvailable = false,
  }: {
    taskId: string;
    checklists?: Checklist[];
    /** The row behind these lists changed on the server; the host re-reads it. */
    onchange?: () => void | Promise<void>;
    /** Draw "stappen schrijven met schakl" (`aiEnabled(user, "task_assist")`). */
    aiAvailable?: boolean;
  } = $props();

  const inputClass =
    "min-w-0 flex-1 rounded-lg border border-border px-2 py-1 text-sm outline-none focus:border-brand";

  let error = $state<string | null>(null);
  let busyKey = $state<string | null>(null);
  let newListTitle = $state("");
  let newItemTitles = $state<Record<string, string>>({});
  let editingId = $state<string | null>(null);
  let editingTitle = $state("");
  let editInput = $state<HTMLInputElement | null>(null);
  let generating = $state(false);

  async function call(
    key: string,
    method: "POST" | "PATCH" | "DELETE",
    path: string,
    body?: unknown,
  ): Promise<boolean> {
    busyKey = key;
    error = null;
    try {
      const res = await fetch(`/api/v1/tasks/${taskId}${path}`, {
        method,
        headers: {
          accept: "application/json",
          ...(body !== undefined ? { "content-type": "application/json" } : {}),
        },
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
      if (!res.ok) {
        const payload = await res.json().catch(() => null);
        error = payload?.error?.message ?? "errors.server";
        return false;
      }
      await onchange?.();
      return true;
    } catch {
      error = "errors.server";
      return false;
    } finally {
      busyKey = null;
    }
  }

  async function toggle(checklist: Checklist, item: ChecklistItem) {
    // Flip first, then let the PATCH catch up — the card's own rule for its most-repeated
    // gesture; a refusal puts the box back.
    const next = !item.done;
    item.done = next;
    const ok = await call(`toggle:${item.id}`, "PATCH", `/checklists/${checklist.id}/items/${item.id}`, {
      done: next,
    });
    if (!ok) item.done = !next;
  }

  async function addItem(checklist: Checklist) {
    const title = (newItemTitles[checklist.id] ?? "").trim();
    if (!title) return;
    const ok = await call(`add:${checklist.id}`, "POST", `/checklists/${checklist.id}/items`, {
      title,
    });
    if (ok) newItemTitles = { ...newItemTitles, [checklist.id]: "" };
  }

  async function addChecklist() {
    const title = newListTitle.trim();
    if (!title) return;
    const ok = await call("addList", "POST", "/checklists", { title });
    if (ok) newListTitle = "";
  }

  async function startEdit(item: ChecklistItem) {
    editingId = item.id;
    editingTitle = item.title;
    await tick();
    editInput?.focus();
    editInput?.select();
  }

  async function commitEdit(checklist: Checklist, item: ChecklistItem) {
    if (editingId !== item.id) return;
    const title = editingTitle.trim();
    editingId = null;
    if (!title || title === item.title) return;
    const previous = item.title;
    item.title = title;
    const ok = await call(`edit:${item.id}`, "PATCH", `/checklists/${checklist.id}/items/${item.id}`, {
      title,
    });
    if (!ok) item.title = previous;
  }

  function cancelEdit() {
    editingId = null;
  }

  async function removeItem(checklist: Checklist, item: ChecklistItem) {
    await call(`del:${item.id}`, "DELETE", `/checklists/${checklist.id}/items/${item.id}`);
  }

  async function generate() {
    if (generating) return;
    generating = true;
    error = null;
    try {
      const res = await fetch(`/api/v1/tasks/${taskId}/ai/checklist`, {
        method: "POST",
        headers: { "content-type": "application/json", accept: "application/json" },
        body: JSON.stringify({}),
      });
      if (!res.ok) {
        const payload = await res.json().catch(() => null);
        error = payload?.error?.message ?? "errors.ai_provider_error";
        return;
      }
      await onchange?.();
    } catch {
      error = "errors.ai_provider_error";
    } finally {
      generating = false;
    }
  }
</script>

<div class="space-y-3">
  <div class="flex items-center justify-between gap-2">
    <h3 class="text-sm font-medium text-text">{t("tasks.checklist.title")}</h3>
    {#if aiAvailable}
      <Button
        type="button"
        size="xs"
        variant="secondary"
        loading={generating}
        onclick={generate}
        title={t("tasks.ai.checklist_generate_hint")}
      >
        <Sparkles size={12} class="text-brand" aria-hidden="true" />
        {generating ? t("tasks.ai.checklist_busy") : t("tasks.ai.checklist_generate")}
      </Button>
    {/if}
  </div>
  {#each checklists as checklist (checklist.id)}
    {@const items = checklist.items ?? []}
    {@const done = items.filter((item) => item.done).length}
    <div>
      <div class="flex items-baseline justify-between gap-2">
        <h4 class="text-sm font-medium text-text">{checklist.title}</h4>
        {#if items.length > 0}
          <span class="shrink-0 text-xs tabular-nums text-text-muted">
            {t("tasks.checklist.progress", { done, total: items.length })}
          </span>
        {/if}
      </div>
      {#if checklist.description}
        <div class="mt-1 text-sm"><Markdown value={checklist.description} /></div>
      {/if}
      <ul class="mt-2 space-y-1">
        {#each items as item (item.id)}
          <li class="group flex items-start gap-2">
            <button
              type="button"
              class="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border text-[10px]
              {item.done
                ? 'border-brand bg-brand text-white'
                : 'border-border text-transparent hover:border-brand'}"
              aria-label={t("tasks.toggle_done")}
              aria-pressed={item.done}
              disabled={busyKey === `toggle:${item.id}`}
              onclick={() => toggle(checklist, item)}>✓</button
            >
            <div class="min-w-0 flex-1">
              {#if editingId === item.id}
                <input
                  bind:this={editInput}
                  bind:value={editingTitle}
                  class="w-full rounded-lg border border-brand px-2 py-0.5 text-sm outline-none"
                  aria-label={t("common.edit")}
                  onkeydown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      void commitEdit(checklist, item);
                    } else if (event.key === "Escape") {
                      event.preventDefault();
                      event.stopPropagation();
                      cancelEdit();
                    }
                  }}
                  onblur={() => commitEdit(checklist, item)}
                />
              {:else}
                <button
                  type="button"
                  class="w-full cursor-text rounded px-1 text-left text-sm hover:bg-surface {item.done
                    ? 'text-text-muted line-through'
                    : 'text-text'}"
                  title={t("tasks.review.item_edit_hint")}
                  onclick={() => startEdit(item)}>{item.title}</button
                >
              {/if}
              {#if item.description}
                <div class="px-1 text-xs text-text-muted">
                  <Markdown value={item.description} />
                </div>
              {/if}
            </div>
            <button
              type="button"
              class="shrink-0 rounded p-0.5 text-text-muted opacity-0 hover:text-red-600 focus:opacity-100 group-hover:opacity-100"
              aria-label={t("common.delete")}
              disabled={busyKey === `del:${item.id}`}
              onclick={() => removeItem(checklist, item)}
            >
              <Trash2 size={13} />
            </button>
          </li>
        {/each}
      </ul>
      <form
        class="mt-2 flex gap-2"
        onsubmit={(event) => {
          event.preventDefault();
          void addItem(checklist);
        }}
      >
        <input
          value={newItemTitles[checklist.id] ?? ""}
          oninput={(event) =>
            (newItemTitles = { ...newItemTitles, [checklist.id]: event.currentTarget.value })}
          placeholder={t("tasks.checklist.item_placeholder")}
          aria-label={t("tasks.checklist.item_placeholder")}
          class={inputClass}
        />
        <Button variant="secondary" size="xs" loading={busyKey === `add:${checklist.id}`}
          >＋</Button
        >
      </form>
    </div>
  {/each}
  <form
    class="flex gap-2"
    onsubmit={(event) => {
      event.preventDefault();
      void addChecklist();
    }}
  >
    <input
      bind:value={newListTitle}
      placeholder={t("tasks.checklist.add")}
      aria-label={t("tasks.review.checklist_add")}
      class="min-w-0 flex-1 rounded-lg border border-dashed border-border px-3 py-1.5 text-sm outline-none focus:border-brand"
    />
    <Button variant="secondary" size="sm" loading={busyKey === "addList"}>
      {t("common.create")}
    </Button>
  </form>
  {#if error}
    <p class="text-sm text-red-600 dark:text-red-400" role="alert">{t(error)}</p>
  {/if}
</div>
