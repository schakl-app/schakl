<script lang="ts">
  /**
   * Log hours **without leaving the record you are looking at** (#402).
   *
   * Every other "record something about this client" on the client hub is a dialog — a
   * contactmoment, a template, the record's own fields, a contact person. Hours were the
   * exception: both entry points were `<a href="/time?company=…">`, and the deep link (a real
   * improvement over landing on the form with whatever client was last used) made the trip
   * one-way. The colleague who came off the phone, wrote down twenty minutes and then wanted the
   * client's domains had to navigate back through Klanten and find them again.
   *
   * So this is the module's own form (`EntryForm`, the same one `/time` and Overzicht mount)
   * in a modal, hosted by whatever page shows the record. Three things it does not do:
   *
   * - **It does not fetch anything until it is opened.** The lookups are four list reads, and
   *   most visits to a client never log an hour (#314, `docs/PERFORMANCE.md`).
   * - **It does not autosave a draft.** `/time`'s create form owns the day's draft (#44); a
   *   second writer on the same day would fight it.
   * - **It does not close on a refusal.** Closing would take the user's typing with it and
   *   leave a page that looks exactly like a successful save — the client hub's edit surface
   *   settled that already.
   *
   * **Host contract:** the page must expose `?/createEntry` (`lib/modules/time/actions.server.ts`).
   */
  import { untrack } from "svelte";

  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import type { EntityPanelLookups } from "$lib/core/registry";
  import { getTimeZone } from "$lib/core/timezone";
  import Modal from "$lib/core/ui/Modal.svelte";
  import { toastSuccess } from "$lib/core/ui/toast.svelte";

  import EntryForm from "./EntryForm.svelte";

  let {
    open = $bindable(false),
    companyId,
    action = "?/createEntry",
  }: {
    open?: boolean;
    /** Preselected in the form's client picker — a default that is visible and changeable. */
    companyId: string;
    /** The host's create action, when it is not mounted at the conventional name. */
    action?: string;
  } = $props();

  /** Exactly what a typed entity panel is handed, minus the members nothing here draws — one
   *  declaration of "the four lists an entry form needs", rather than a fifth copy of it. */
  type Lookups = Omit<EntityPanelLookups, "members">;

  let lookups = $state<Lookups | null>(null);
  let phase = $state<"idle" | "loading" | "failed">("idle");
  /** Bumped per open, so a second visit gets an empty form rather than the last one's leftovers. */
  let session = $state(0);

  async function ensureLookups(): Promise<void> {
    if (lookups || phase === "loading") return;
    phase = "loading";
    try {
      const response = await fetch("/time/lookups", { headers: { accept: "application/json" } });
      if (!response.ok) throw new Error(String(response.status));
      lookups = (await response.json()) as Lookups;
      phase = "idle";
    } catch {
      // A dialog that spins forever is worse than one that says so and offers the page that
      // does not need this call (#253 — a control must not silently refuse).
      phase = "failed";
    }
  }

  $effect(() => {
    if (!open) return;
    // `ensureLookups` reads the very state it writes; tracking that would re-run this effect on
    // its own assignment and fire a second fetch on the first open.
    untrack(() => {
      session += 1;
      void ensureLookups();
    });
  });

  /** Today on the org's wall clock (CLAUDE.md §8) — `en-CA` formats as YYYY-MM-DD. */
  const today = $derived(
    new Intl.DateTimeFormat("en-CA", { timeZone: getTimeZone() }).format(new Date()),
  );
</script>

<Modal bind:open title={t("time.new_registration")}>
  {#if lookups}
    {#key session}
      <EntryForm
        {action}
        date={today}
        companies={lookups.companies}
        projects={lookups.projects}
        tasks={lookups.tasks}
        taskStatuses={lookups.taskStatuses}
        defaultCompanyId={companyId}
        error={(page.form as { error?: string } | null)?.error ?? null}
        oncancel={() => (open = false)}
        ondone={(saved) => {
          if (!saved) return;
          open = false;
          toastSuccess(t("time.entry_saved"));
        }}
      />
    {/key}
  {:else if phase === "failed"}
    <p class="text-sm text-text-muted">{t("time.log_dialog.load_failed")}</p>
    <a
      href={`/time?company=${companyId}`}
      class="mt-3 inline-block text-sm font-medium text-brand hover:underline"
    >
      {t("time.log_dialog.open_timesheet")}
    </a>
  {:else}
    <p class="text-sm text-text-muted">{t("common.loading")}</p>
  {/if}
</Modal>
