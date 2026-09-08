<script lang="ts">
  /**
   * "Klant verwijderen?" — the one dialog behind every Verwijderen on a client (docs/TRASH.md).
   *
   * It asks the API what deleting *this* client would do before it asks the user anything,
   * because the honest answer depends on facts the row does not show: a client with an issued
   * invoice, a domain, an agreement, a project or logged hours **cannot** be deleted — those
   * records outlive it — and a client with none of those goes to the trash, where it can be
   * brought back for thirty days. So the dialog is a choice between two ways out, stated in the
   * shape the invoice's cancel dialog established (docs/UX.md): a radio that posts as `mode`,
   * each option with a one-line hint, the confirm button changing label and colour with the pick.
   *
   * **Archiveren is the default and it is not red.** It destroys nothing and it is what a client
   * you are done with should get (#405's "the gentler action goes above the destructive one"). The
   * trash option is disabled — with the reason, in numbers — for a client with a history, so the
   * control stays visible and says why it is not for this row rather than vanishing (#253's rule
   * is about a control that can *only* refuse; this one is paired with the one that works).
   */
  import { t, tn } from "$lib/core/i18n";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";

  type Mode = "archive" | "trash";
  interface DependentCount {
    key: string;
    label_key: string;
    count: number;
  }
  interface Preview {
    can_trash: boolean;
    blocking: DependentCount[];
    taken_along: DependentCount[];
    retention_days: number;
  }

  let {
    open = $bindable(false),
    companyId,
    name,
    status,
    action = "?/delete",
    fields = {},
    onfailure,
  }: {
    open?: boolean;
    companyId: string;
    name: string;
    /** The client's lifecycle status: an archived client has no archive to offer. */
    status: string;
    action?: string;
    fields?: Record<string, string>;
    onfailure?: (errorKey: string) => void;
  } = $props();

  let preview = $state<Preview | null>(null);
  let loadFailed = $state(false);
  let mode = $state<Mode>("archive");

  const archived = $derived(status === "archived");
  const blocked = $derived(preview !== null && !preview.can_trash);

  function listOf(rows: DependentCount[]): string {
    return rows.map((row) => tn(row.label_key, row.count)).join(", ");
  }

  // Every opening re-reads: what hangs off a client changes between two presses of the same
  // button, and a stale "nothing attached" is exactly the wrong thing to be confident about.
  $effect(() => {
    if (!open) return;
    preview = null;
    loadFailed = false;
    mode = archived ? "trash" : "archive";
    const id = companyId;
    void (async () => {
      try {
        const res = await fetch(`/api/v1/trash/company/${id}/preview`, {
          headers: { accept: "application/json" },
        });
        if (!res.ok) throw new Error(String(res.status));
        const body = (await res.json()) as Preview;
        preview = body;
        // An archived client has only the trash left; one with a history has only the archive.
        if (!body.can_trash && !archived) mode = "archive";
      } catch {
        loadFailed = true;
      }
    })();
  });

  const message = $derived.by(() => {
    if (preview === null) return loadFailed ? t("errors.server") : t("companies.delete.loading");
    if (!preview.can_trash) {
      return t("companies.delete.blocked", { name, blocking: listOf(preview.blocking) });
    }
    return t("companies.delete.free", { name });
  });
  const consequences = $derived.by(() => {
    if (preview === null || mode !== "trash" || preview.taken_along.length === 0) return [];
    return [t("companies.delete.taken_along", { items: listOf(preview.taken_along) })];
  });
  // The confirm is disabled until the preview has answered: pressing a red button on a client
  // whose cost is still being counted is the accident this dialog exists to prevent.
  const ready = $derived(preview !== null && (mode === "archive" ? !archived : preview.can_trash));
</script>

<ConfirmDialog
  bind:open
  title={t("companies.delete.title")}
  {message}
  {action}
  fields={{ ...fields, mode }}
  {consequences}
  confirmLabel={mode === "trash"
    ? t("companies.delete.confirm_trash")
    : t("companies.delete.confirm_archive")}
  variant={mode === "trash" ? "danger" : "primary"}
  confirmDisabled={!ready}
  {onfailure}
>
  <fieldset class="mt-4 space-y-2" data-testid="delete-mode" disabled={preview === null}>
    <label
      class="flex gap-3 rounded-lg border border-border p-3 has-[:checked]:border-brand has-[:disabled]:opacity-60 {archived
        ? ''
        : 'cursor-pointer'}"
    >
      <input
        type="radio"
        name="mode_pick"
        value="archive"
        bind:group={mode}
        disabled={archived}
        class="mt-1 accent-brand"
      />
      <span class="text-sm">
        <span class="font-medium text-text">{t("companies.delete.archive")}</span>
        <span class="mt-0.5 block text-text-muted">
          {archived ? t("companies.delete.archived_already") : t("companies.delete.archive_hint")}
        </span>
      </span>
    </label>
    <label
      class="flex gap-3 rounded-lg border border-border p-3 has-[:checked]:border-brand has-[:disabled]:opacity-60 {blocked
        ? ''
        : 'cursor-pointer'}"
    >
      <input
        type="radio"
        name="mode_pick"
        value="trash"
        bind:group={mode}
        disabled={blocked}
        class="mt-1 accent-brand"
      />
      <span class="text-sm">
        <span class="font-medium text-text">{t("companies.delete.trash")}</span>
        <span class="mt-0.5 block text-text-muted">
          {#if blocked && preview}
            {t("companies.delete.trash_blocked_hint", { blocking: listOf(preview.blocking) })}
          {:else}
            {t("companies.delete.trash_hint", { days: preview?.retention_days ?? 30 })}
          {/if}
        </span>
      </span>
    </label>
  </fieldset>
</ConfirmDialog>
