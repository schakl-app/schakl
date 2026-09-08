<script lang="ts">
  /**
   * Instellingen → Prullenbak (docs/TRASH.md).
   *
   * A row says four things: what it is and was called, who deleted it and when, what happens
   * to it next (gone for good on a date — or kept, because something that must outlive it has
   * appeared), and what went with it. **Terugzetten** is inline and not red: it destroys
   * nothing and is the reason the screen exists. **Definitief verwijderen** sits behind the ⋯
   * and asks for a tick, not a click — it is the one action in the product that is both
   * irreversible and invisible in its cost (docs/UX.md, `ConfirmDialog.acknowledge`).
   */
  import { Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { fmtDateTime, fmtNumericDate } from "$lib/core/format";
  import { t, tn } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import { trashEntity } from "$lib/core/trash/entities";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import { toastError, toastSuccess } from "$lib/core/ui/toast.svelte";

  let { data, form } = $props();

  type Item = (typeof data.items)[number];

  const busy = new InFlight();
  let purging = $state<Item | null>(null);
  let confirmPurge = $state(false);

  function listOf(rows: Item["taken_along"] | undefined): string {
    return (rows ?? []).map((row) => tn(row.label_key, row.count)).join(", ");
  }

  let announced = $state<string | null>(null);
  $effect(() => {
    const restored = form?.restored;
    if (restored && announced !== `r:${restored.id}`) {
      announced = `r:${restored.id}`;
      toastSuccess(t("companies.restored_toast", { name: restored.label }));
    }
    const purged = form?.purged;
    if (purged && announced !== `p:${purged.id}`) {
      announced = `p:${purged.id}`;
      toastSuccess(t("trash.purge"));
    }
  });

  function askPurge(item: Item) {
    purging = item;
    confirmPurge = true;
  }
</script>

<svelte:head>
  <title>{pageTitle(t("trash.title"))}</title>
</svelte:head>

<div class="mb-6">
  <h1 class="mt-2 text-xl font-semibold text-text">{t("trash.title")}</h1>
  <p class="mt-1 text-sm text-text-muted">{t("trash.subtitle", { days: data.retentionDays })}</p>
</div>

{#if data.items.length === 0}
  <div class="rounded-xl border border-dashed border-border bg-surface-raised p-10 text-center">
    <Trash2 size={20} class="mx-auto text-text-muted" aria-hidden="true" />
    <p class="mt-2 text-sm text-text-muted">{t("trash.empty")}</p>
  </div>
{:else}
  {#if data.highlight && data.items.some((item) => item.entity_id === data.highlight)}
    <p class="mb-3 text-sm text-text-muted">{t("trash.highlighted")}</p>
  {/if}
  <ul class="divide-y divide-border rounded-xl border border-border bg-surface-raised">
    {#each data.items as item (item.entity_type + item.entity_id)}
      {@const kind = trashEntity(item.entity_type)}
      {@const highlighted = item.entity_id === data.highlight}
      <li
        class="flex items-start gap-3 p-4 {highlighted ? 'bg-brand/5' : ''}"
        data-testid="trash-row"
        data-highlighted={highlighted ? "true" : undefined}
      >
        <Trash2 size={18} class="mt-0.5 shrink-0 text-text-muted" aria-hidden="true" />
        <div class="min-w-0 flex-1">
          <div class="flex flex-wrap items-center gap-2">
            <span class="rounded-full bg-surface px-2 py-0.5 text-xs text-text-muted">
              {kind ? t(kind.labelKey) : item.entity_type}
            </span>
            <span class="truncate font-medium text-text">{item.label}</span>
          </div>
          <p class="mt-1 text-sm text-text-muted">
            {t("trash.deleted_by", {
              actor: item.deleted_by_name ?? t("activity.system"),
              date: fmtDateTime(item.deleted_at),
            })}
          </p>
          {#if (item.blocking ?? []).length > 0}
            <p class="mt-0.5 text-sm text-amber-700 dark:text-amber-400">
              {t("trash.kept", { blocking: listOf(item.blocking) })}
            </p>
          {:else}
            <p class="mt-0.5 text-sm text-text-muted">
              {t("trash.purge_at", { date: fmtNumericDate(item.purge_at.slice(0, 10)) })}
            </p>
          {/if}
          {#if (item.taken_along ?? []).length > 0}
            <p class="mt-0.5 text-xs text-text-muted">
              {t("trash.taken_along", { items: listOf(item.taken_along) })}
            </p>
          {/if}
        </div>
        <form
          method="POST"
          action="?/restore"
          use:enhance={busy.wrap(`restore:${item.entity_id}`, () => async ({ result, update }) => {
            if (result.type === "failure") {
              toastError(
                t(
                  String((result.data as { error?: string } | undefined)?.error ?? "errors.server"),
                ),
              );
              return;
            }
            await update({ reset: false });
          })}
        >
          <input type="hidden" name="entity_type" value={item.entity_type} />
          <input type="hidden" name="id" value={item.entity_id} />
          <input type="hidden" name="label" value={item.label} />
          <Button variant="secondary" size="sm" loading={busy.is(`restore:${item.entity_id}`)}>
            {t("trash.restore")}
          </Button>
        </form>
        {#if (item.blocking ?? []).length === 0}
          <ActionsMenu
            compact
            items={[
              {
                label: t("trash.purge"),
                icon: Trash2,
                danger: true,
                onclick: () => askPurge(item),
              },
            ]}
          />
        {/if}
      </li>
    {/each}
  </ul>
  {#if data.total > data.items.length}
    <p class="mt-3 text-sm text-text-muted">
      {t("trash.more", { shown: data.items.length, total: data.total })}
    </p>
  {/if}
{/if}

{#if purging}
  <ConfirmDialog
    bind:open={confirmPurge}
    title={t("trash.purge_confirm.title")}
    message={t("trash.purge_confirm.message", { name: purging.label })}
    consequences={(purging.taken_along ?? []).length > 0
      ? [t("trash.taken_along", { items: listOf(purging.taken_along) })]
      : []}
    acknowledge={t("trash.purge_confirm.acknowledge", { name: purging.label })}
    action="?/purge"
    fields={{ entity_type: purging.entity_type, id: purging.entity_id }}
    confirmLabel={t("trash.purge")}
    onfailure={(key) => toastError(t(key))}
  />
{/if}
