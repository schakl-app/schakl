<script lang="ts">
  /**
   * The client hub (#364).
   *
   * It used to be *composed* and nothing else: the registry handed the page a list of panels, the
   * page drew each as a full-width card in `position` order, and that was the whole layout. Every
   * panel was equally important, so none of them was — a card holding eight invoices and a card
   * saying *"Deze klant heeft nog geen Drive-map."* were the same width, the same weight and cost
   * the same to scroll past. A well-filled client ran 4.6 screens; a young one ran 2.9, ten of its
   * fourteen cards being a heading over a negative sentence.
   *
   * The page answers four questions now, and the first without scrolling.
   *
   * 1. **Who is this, and are we all right with them?** — the header, plus the vital-signs strip
   *    (`SummaryStrip`), which is contributed through the registry exactly as the panels are.
   * 2. **What is the working surface?** — panels the module marked `prominence: "primary"`.
   * 3. **What is on file?** — the registers, in a narrower second lane under their own heading,
   *    because a two-word row does not want 1150 px.
   * 4. **What has this client got nothing of yet?** — one strip of ＋ chips, not ten empty cards.
   *
   * §6 is intact: the page still composes whatever the registry hands it. It has only stopped
   * pretending a Drive link and an invoice ledger want the same box.
   */
  import { ChevronDown, ListChecks, Pencil, Plus, Trash2 } from "@lucide/svelte";

  import { SvelteSet } from "svelte/reactivity";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { aiEnabled } from "$lib/core/ai";
  import CompanyAIActions from "$lib/core/ai/CompanyAIActions.svelte";
  import { clearEditIntent, editIntent } from "$lib/core/edit-intent";
  import { t } from "$lib/core/i18n";
  import { memberLabel } from "$lib/core/members";
  import { pageTitle } from "$lib/core/title";
  import { can } from "$lib/core/permissions";
  import { companyPanelComponent } from "$lib/core/registry";
  import { InFlight } from "$lib/core/submit.svelte";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Assignees from "$lib/core/ui/Assignees.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import { filedrop } from "$lib/core/ui/filedrop";
  import Modal from "$lib/core/ui/Modal.svelte";
  import SlideOver from "$lib/core/ui/SlideOver.svelte";
  import SummaryStrip from "$lib/core/ui/SummaryStrip.svelte";
  import { toastError, toastSuccess } from "$lib/core/ui/toast.svelte";
  import CompanyForm from "$lib/modules/companies/CompanyForm.svelte";
  import { COMPANY_STATUSES, statusPillClass } from "$lib/modules/companies/status";
  import ContactDraftField from "$lib/modules/contacts/ContactDraftField.svelte";
  import InteractionForm from "$lib/modules/interactions/InteractionForm.svelte";
  import LogTimeDialog from "$lib/modules/time/LogTimeDialog.svelte";
  import TaskQuickCreate from "$lib/modules/tasks/TaskQuickCreate.svelte";

  let { data, form } = $props();

  // Panels are contributed by enabled modules and composed here — the "attach to company" hub.
  const enabled = $derived(page.data.theme?.enabledModules ?? []);
  const company = $derived(data.company);
  const assignees = $derived(company.assignees ?? []);
  /** The legal name, but only where it is a *second* fact — see the header comment below. */
  const legalName = $derived.by(() => {
    const value = (company.legal_name ?? "").trim();
    return value && value !== company.name ? value : null;
  });

  // The edit surface's lookups stream in behind the page (#290) — nothing here draws them, and
  // most visits never open it. Held in state rather than awaited in the markup: a re-run
  // load hands us a *new* promise, and an `{#await}` would fall back to its pending branch and
  // remount the form, throwing away what the user had picked.
  let editForm = $state<Awaited<typeof data.editForm> | null>(null);
  $effect(() => {
    void data.editForm.then((resolved) => (editForm = resolved));
  });

  // The contact persons currently on this client, primary first — derived from the org's contacts
  // rather than fetched again, since each one carries the companies it is linked to.
  const companyContacts = $derived(
    (editForm?.contacts ?? [])
      .map((c) => ({ id: c.id, link: c.companies?.find((l) => l.company_id === company.id) }))
      .filter((c) => c.link !== undefined)
      .map((c) => ({ contact_id: c.id, is_primary: Boolean(c.link?.is_primary) }))
      .sort((a, b) => Number(b.is_primary) - Number(a.is_primary)),
  );

  // Opened straight into edit when reached from the overview's ⋯ → Bewerken (#78).
  let showEdit = $state(editIntent());
  let confirmDelete = $state(false);
  // #348: the task-template picker used to sit bare under the status chip — an unlabelled
  // native <select> whose only visible text was the first template's name ("Onboarding", which
  // is also a company status), so the page read as if this client held two statuses. It is an
  // action, so it lives with the actions and names itself before it is used.
  let showTemplate = $state(false);
  let templateId = $state("");
  const busy = new InFlight();

  // `?edit=1` is an *intent*, consumed once. Left in the URL, a reload after saving dropped the
  // user straight back into the form and the save looked like it had not happened.
  //
  // Cleared when the surface **closes**, not on mount: `replaceState` during hydration throws
  // ("Cannot call replaceState before router is initialized") and a throw there takes the rest
  // of the hydration pass with it. Closing is a user gesture, so the router is long since up —
  // and it covers every way out, including SlideOver's own ✕, Escape and backdrop.
  let editWasOpen = $state(false);
  $effect(() => {
    if (showEdit) {
      editWasOpen = true;
      return;
    }
    if (!editWasOpen) return;
    editWasOpen = false;
    clearEditIntent();
  });

  // Header actions render only for holders of the matching permission (#253).
  const canWrite = $derived(can(page.data.user, "companies.company.write"));
  const canDelete = $derived(can(page.data.user, "companies.company.delete"));
  const canApplyTemplate = $derived(
    data.templates.length > 0 && can(page.data.user, "tasks.template.apply"),
  );

  // ---- the composed page ------------------------------------------------- //
  // The API says what each panel *is* (#364): a working surface or a register, full or half
  // width, and whether this client has anything in it yet. The page sorts on those answers and
  // knows the name of no module.
  type Panel = (typeof data.panels)[number];
  // A chip whose module has no screen of its own to send you to unfolds its card here instead
  // (see the strip below), so nothing an empty panel offered is lost by absorbing it.
  const unfolded = new SvelteSet<string>();
  const drawn = $derived(data.panels.filter((p: Panel) => !p.empty || unfolded.has(p.key)));
  const primary = $derived(drawn.filter((p: Panel) => p.prominence === "primary"));
  const registers = $derived(drawn.filter((p: Panel) => p.prominence !== "primary"));
  const empties = $derived(data.panels.filter((p: Panel) => p.empty && !unfolded.has(p.key)));

  /**
   * How the cards fit together.
   *
   * `size` said how wide a panel wants to be and the page drew one two-column grid, which is a
   * *row* layout: every row is as tall as its tallest card, so a short card leaves the gap under
   * it empty, and a full-width panel after an odd number of halves leaves half a row empty beside
   * the one before it. On a real client that came to a 271 px void under Abonnementen, a whole
   * empty half-row beside Uren, and four ragged bottom edges — the page looked unfinished rather
   * than composed, which is the same complaint #364 set out to fix, one layer down.
   *
   * A row layout cannot answer it, so the ordered panels are cut into **blocks** and each block
   * gets the layout its own count deserves. Three kinds, and each one is hole-free by
   * construction rather than by luck:
   *
   * - `solo` — a full-width panel, **or a half-width one with no half-width neighbour**. A card
   *   alone on its row takes the row; the alternative is a bordered rectangle beside nothing.
   * - `pair` — exactly two halves, in a two-column grid that *stretches*. With two cards there is
   *   nothing to pack, so the tidy answer is a matched pair: bottoms level, one edge, no gap.
   * - `stack` — three or more halves, packed by CSS multi-column. Each card keeps its natural
   *   height and the browser balances the two lanes, which is real masonry with no measuring, no
   *   layout jump after hydration, and no second opinion about a height the browser already knows.
   *
   * `items-start` is therefore gone, and so is stretching a two-row list to match a tall card
   * beside it — the mistake the old comment here rightly warned about. What replaced it is not
   * "stretch everything": a pair matches because a pair has nothing else to do, and a stack packs
   * because packing is what three cards want.
   *
   * One constraint a contributing module should know: a `stack` is a fragmentation context, so a
   * panel that opens a dropdown wants `prominence: primary` (where pairs and solos live) or a
   * popover anchored to the viewport. No panel does that today.
   */
  type Block = { key: string; kind: "solo" | "pair" | "stack"; panels: Panel[] };

  function blocksOf(panels: Panel[]): Block[] {
    const runs: Panel[][] = [];
    for (const panel of panels) {
      const run = runs.at(-1);
      // A full-width panel always starts (and ends) its own run; halves collect into one.
      if (panel.size !== "half" || !run || run[0].size !== "half") runs.push([panel]);
      else run.push(panel);
    }
    return runs.map((run) => ({
      key: run[0].key,
      kind:
        run[0].size !== "half" || run.length === 1 ? "solo" : run.length === 2 ? "pair" : "stack",
      panels: run,
    }));
  }

  // ---- tier 1: the status pill edits in place ---------------------------- //
  // Most real edits are one field, and none of them should cost a dialog, a scroll position and
  // a full page invalidation. The pill was already the right control in the right place; it just
  // did not do anything.
  let editingStatus = $state(false);
  let statusValue = $state(company.status);
  let statusForm: HTMLFormElement | undefined = $state();

  /**
   * Submit the pick, one frame later.
   *
   * `onselect` fires *before* the binding it is about has propagated into `Combobox`'s hidden
   * input, so submitting straight from the handler posts the value that was there when the
   * dropdown opened — the pill flicked back to what it already said and the PATCH was a no-op.
   * The same one-frame wait `LinkField` uses for exactly this reason.
   */
  function submitStatus(): void {
    requestAnimationFrame(() => statusForm?.requestSubmit());
  }

  // Log a contactmoment from the header — quick-add where the user is (docs/UX.md), with the
  // client pinned. The panel's own ＋ stays; this is the reachable top-of-page entry.
  let showLogInteraction = $state(false);
  const canLogInteraction = $derived(
    enabled.includes("interactions") && can(page.data.user, "interactions.interaction.write"),
  );
  const mentionCandidates = $derived(
    data.members.map((m) => ({ id: m.user_id, name: memberLabel(m) })),
  );

  // Uren boeken from the header (#402): the time module's own form in a dialog, hosted here the
  // way the contactmoment form already is. Nothing is fetched until it opens.
  let loggingTime = $state(false);

  // Nieuwe taak from the header (#391): the shared dialog, pre-linked to this client and opening
  // with yourself on the roster — the assignee this button always chose, now visible.
  let creatingTask = $state(false);

  // AI digest + report drafts (#130): rendered only when the reporting feature is on.
  const hasReporting = $derived(aiEnabled(page.data.user, "reporting"));
</script>

<svelte:head>
  <title>{pageTitle(company.name)}</title>
</svelte:head>

<div class="mb-5">
  <div class="mt-2 flex flex-wrap items-start justify-between gap-3">
    <div class="min-w-0">
      <div class="flex flex-wrap items-center gap-3">
        {#if company.logo_file_id}
          <!-- The client's own logo (#196) — client data, never tenant branding. -->
          <img
            src={`/api/v1/companies/${company.id}/logo`}
            alt=""
            class="h-9 w-9 shrink-0 rounded-lg border border-border object-contain"
          />
        {/if}
        <h1 class="text-xl font-semibold text-text">{company.name}</h1>

        {#if editingStatus}
          <!-- One field, changed where it is shown, PATCHed on pick. -->
          <form
            method="POST"
            action="?/update"
            class="w-44"
            bind:this={statusForm}
            use:enhance={busy.wrap("status", () => async ({ update, result }) => {
              editingStatus = false;
              // `keep`: this edits a value that already exists, and there is nothing to reset to.
              await update({ reset: false });
              if (result.type === "success") toastSuccess(t("companies.status_saved"));
            })}
          >
            <Combobox
              items={COMPANY_STATUSES.map((option) => ({
                value: option,
                label: t(`companies.status.${option}`),
              }))}
              name="status"
              id="company-status-inline"
              ariaLabel={t("companies.field.status")}
              bind:value={statusValue}
              allowEmpty={false}
              listClass="w-56"
              onselect={submitStatus}
            />
          </form>
        {:else}
          <svelte:element
            this={canWrite ? "button" : "span"}
            type={canWrite ? "button" : undefined}
            onclick={canWrite ? () => (editingStatus = true) : undefined}
            title={canWrite ? t("companies.field.status") : undefined}
            class="rounded-full px-2.5 py-0.5 text-xs font-medium {statusPillClass(
              company.status,
            )} {canWrite ? 'cursor-pointer hover:ring-1 hover:ring-current/40' : ''}"
          >
            {t(`companies.status.${company.status}`)}
          </svelte:element>
        {/if}
      </div>
      <!-- The name a document is addressed to, and only when it is not the one above it: the
           API sends `null` for "the label is also the legal name", so most clients draw nothing
           here and the header stays one name long. Where it *does* differ, this is the one
           screen that has to say so — the invoice this client gets will be headed with a name
           the H1 does not contain, and nobody should have to open the billing card to discover
           that. Muted and prefixed, so it reads as a fact about the record rather than as a
           second title. -->
      {#if legalName}
        <p class="mt-1 text-sm text-text-muted">
          {t("companies.legal_name")}: <span class="text-text">{legalName}</span>
        </p>
      {/if}
      {#if company.website}
        <a
          href={company.website.startsWith("http") ? company.website : `https://${company.website}`}
          target="_blank"
          rel="noopener noreferrer"
          class="mt-1 inline-block text-sm text-text-muted hover:text-brand">{company.website} ↗</a
        >
      {/if}
      {#if assignees.length > 0}
        <p class="mt-1 flex flex-wrap items-center gap-2 text-sm text-text-muted">
          <span>{t("companies.field.responsible")}:</span>
          <Assignees {assignees} members={data.members} max={6} />
        </p>
      {/if}
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
      {#if can(page.data.user, "tasks.task.create")}
        <!-- Ask for the name first (#391), then create-then-edit for the rest (#230): the shared
             dialog posts a named task pre-linked to this client and lands on its detail page in
             edit mode. -->
        <Button
          variant="secondary"
          size="sm"
          type="button"
          disabled={busy.active}
          onclick={() => (creatingTask = true)}
        >
          {t("companies.actions.new_task")}
        </Button>
      {/if}
      {#if can(page.data.user, "time.entry.write")}
        <!-- The same act as the Uren panel's ＋ (#402), so it is the same control: the module's
             own entry form in a dialog, this client preset, and this page still underneath it
             when it closes. It used to be a link to `/time?company=…` — the client was carried
             through and the way back was not. -->
        <button
          type="button"
          onclick={() => (loggingTime = true)}
          class="rounded-lg border border-border px-3 py-1.5 text-sm text-text-muted hover:border-brand hover:text-brand"
        >
          {t("companies.actions.log_time")}
        </button>
      {/if}
      {#if hasReporting}
        <CompanyAIActions companyId={company.id} companyName={company.name} />
      {/if}
      {#if canWrite || canDelete || canApplyTemplate}
        <ActionsMenu
          items={[
            ...(canWrite
              ? [{ label: t("common.edit"), icon: Pencil, onclick: () => (showEdit = true) }]
              : []),
            ...(canApplyTemplate
              ? [
                  {
                    label: t("companies.actions.apply_template_menu"),
                    icon: ListChecks,
                    onclick: () => (showTemplate = true),
                  },
                ]
              : []),
            ...(canDelete
              ? [
                  {
                    label: t("common.delete"),
                    icon: Trash2,
                    danger: true,
                    onclick: () => (confirmDelete = true),
                  },
                ]
              : []),
          ]}
        />
      {/if}
    </div>
  </div>
  {#if form?.templateApplied}
    <p class="mt-3 text-xs text-green-600 dark:text-green-400">
      {t("companies.template_applied")}
    </p>
  {/if}
</div>

<!-- Are we all right with this client? Five numbers, above the fold, each opening what it counted. -->
<SummaryStrip tiles={data.summary} />

{#snippet card(panel: Panel, heading: 2 | 3)}
  {@const spec = companyPanelComponent(enabled, panel.key)}
  <section id={`panel-${panel.key}`} class="rounded-xl border border-border bg-surface-raised p-5">
    <!-- A panel with a control beside its title draws its own heading row (#364); everything
         else gets the host's, so a module contributing a plain list writes no chrome. -->
    {#if !spec?.ownsHeader}
      {#if heading === 3}
        <h3 class="mb-4 text-sm font-semibold text-text">{t(panel.title_key)}</h3>
      {:else}
        <h2 class="mb-4 text-sm font-semibold text-text">{t(panel.title_key)}</h2>
      {/if}
    {/if}
    {#if spec}
      {@const PanelComponent = spec.component}
      <PanelComponent
        companyId={company.id}
        data={panel.data}
        members={data.members}
        definitions={data.definitions}
        locale={data.locale}
        title={t(panel.title_key)}
        onedit={canWrite ? () => (showEdit = true) : undefined}
      />
    {:else}
      <pre class="overflow-x-auto text-xs text-text-muted">{JSON.stringify(
          panel.data,
          null,
          2,
        )}</pre>
    {/if}
  </section>
{/snippet}

{#snippet lane(panels: Panel[], heading: 2 | 3)}
  <!-- A column of blocks; each block decides how its own cards sit beside each other. Every kind
       collapses to one column below `lg`, so a phone gets one stack and the rule that fits the
       cards together is a desktop rule only. -->
  <div class="flex flex-col gap-4">
    {#each blocksOf(panels) as block (block.key)}
      {#if block.kind === "solo"}
        {@render card(block.panels[0], heading)}
      {:else if block.kind === "pair"}
        <div class="grid gap-4 lg:grid-cols-2">
          {#each block.panels as panel (panel.key)}
            {@render card(panel, heading)}
          {/each}
        </div>
      {:else}
        <!-- `-mb-4` cancels the last card's own bottom margin, which is what a multi-column
             container has instead of `gap` for the vertical direction. -->
        <div class="-mb-4 lg:columns-2 lg:gap-4">
          {#each block.panels as panel (panel.key)}
            <div class="mb-4 break-inside-avoid">
              {@render card(panel, heading)}
            </div>
          {/each}
        </div>
      {/if}
    {/each}
  </div>
{/snippet}

{#if primary.length > 0}
  {@render lane(primary, 2)}
{/if}

{#if registers.length > 0}
  <!-- Reference material: correct, occasionally consulted, never news. It keeps its own lane
       and its own heading so the working surfaces above are unmistakably the foreground. -->
  <h2 class="mb-3 mt-8 text-xs font-semibold uppercase tracking-wide text-text-muted">
    {t("companies.section.registers")}
  </h2>
  {@render lane(registers, 3)}
{/if}

{#if empties.length > 0}
  <!-- "A card is for content; an absence is a sentence, and ten absences are one sentence with
       ten links." A module with nothing to show earns a chip, not a heading, a border and 100 px
       — and ten ＋ actions in one row are easier to find than ten cards to scroll past, so this
       improves discoverability rather than hiding anything. -->
  <section class="mt-8 rounded-xl border border-dashed border-border p-4">
    <h2 class="text-xs font-semibold uppercase tracking-wide text-text-muted">
      {t("companies.section.nothing_yet")}
    </h2>
    <ul class="mt-3 flex flex-wrap gap-2">
      {#each empties as panel (panel.key)}
        {@const spec = companyPanelComponent(enabled, panel.key)}
        {@const label = t(spec?.emptyLabelKey ?? panel.title_key)}
        <li>
          {#if spec?.emptyHref}
            <a
              href={spec.emptyHref(company.id)}
              class="inline-flex items-center gap-1.5 rounded-full border border-border px-3 py-1 text-sm text-text-muted transition-colors hover:border-brand hover:text-brand"
            >
              <Plus size={14} aria-hidden="true" />
              {label}
            </a>
          {:else}
            <!-- No screen of its own to send you to — Drive's "koppel een map" and Google Ads'
                 connect flow live *in the panel*. So the chip unfolds the card in place rather
                 than being an inert label: absorbing an empty panel must not cost the one control
                 that empty panel existed to offer. -->
            <button
              type="button"
              onclick={() => unfolded.add(panel.key)}
              class="inline-flex items-center gap-1.5 rounded-full border border-border px-3 py-1 text-sm text-text-muted transition-colors hover:border-brand hover:text-brand"
            >
              <ChevronDown size={14} aria-hidden="true" />
              {label}
            </button>
          {/if}
        </li>
      {/each}
    </ul>
  </section>
{/if}

{#if canApplyTemplate}
  <Modal bind:open={showTemplate} title={t("companies.actions.apply_template_menu")}>
    <form
      method="POST"
      action="?/applyTemplate"
      use:enhance={busy.wrap("apply-template", () => ({ update }) => {
        showTemplate = false;
        // Applying a template starts something new, so the picker empties: a second client
        // process is a fresh pick, not an edit of the one just applied.
        templateId = "";
        void update({ reset: true });
      })}
      class="space-y-3"
    >
      <div>
        <label for="template_id" class="mb-1 block text-sm font-medium text-text">
          {t("companies.template_label")}
        </label>
        <Combobox
          name="template_id"
          bind:value={templateId}
          allowEmpty={false}
          placeholder={t("companies.template_placeholder")}
          items={data.templates.map((template) => ({
            value: template.id,
            label: template.name,
          }))}
        />
        <p class="mt-1 text-xs text-text-muted">{t("companies.template_hint")}</p>
      </div>
      <div class="flex justify-end gap-2">
        <Button
          type="button"
          variant="secondary"
          onclick={() => (showTemplate = false)}
          disabled={busy.active}
        >
          {t("common.cancel")}
        </Button>
        <Button loading={busy.is("apply-template")} disabled={busy.active || !templateId}>
          {t("companies.actions.apply_template")}
        </Button>
      </div>
    </form>
  </Modal>
{/if}

{#if can(page.data.user, "tasks.task.create")}
  <TaskQuickCreate
    bind:open={creatingTask}
    companyId={company.id}
    members={data.members}
    assignees={page.data.user?.id ? [{ user_id: page.data.user.id, is_primary: true }] : []}
    action="/tasks?/create"
    error={(page.form?.error as string | undefined) ?? null}
    pickerSlot="company_new_task"
  />
{/if}

{#if can(page.data.user, "time.entry.write")}
  <LogTimeDialog bind:open={loggingTime} companyId={company.id} />
{/if}

<Modal bind:open={showLogInteraction} title={t("interactions.add")}>
  <InteractionForm
    prefill={{ company_id: company.id }}
    mentions={mentionCandidates}
    onsaved={() => (showLogInteraction = false)}
  />
</Modal>

{#if canWrite}
  <!-- Tier 3 (#364): the whole record, for creation and for "I want to fill everything in".
       Docked right and full height rather than centred — as a `Modal` this form rendered 1445 px
       tall on a 900 px laptop, so Opslaan started below the fold, and the record you are editing
       against was hidden behind it. -->
  <SlideOver bind:open={showEdit} title={t("companies.edit")} size="2xl">
    <form
      method="POST"
      action="?/update"
      enctype="multipart/form-data"
      use:enhance={busy.wrap("update", () => async ({ update, result }) => {
        await update({ reset: false });
        if (result.type === "success") {
          showEdit = false;
          toastSuccess(t("companies.saved"));
        } else if (result.type === "failure") {
          // The dialog stays open on a refusal — closing it would take the user's typing with
          // it and leave a page that looks exactly like a successful save.
          toastError(t("common.save_failed"));
        }
      })}
      class="space-y-3 p-4"
    >
      <!-- Same component the create form uses: one definition of a client's fields. Every editable
         field is here, contact persons included — an edit surface that hides a field the view
         shows sends you hunting for it (docs/UX.md). -->
      <CompanyForm
        {company}
        members={data.members}
        definitions={data.definitions}
        locale={data.locale}
        idPrefix="edit-company"
      >
        {#if editForm}
          <ContactDraftField
            contacts={editForm.contacts}
            definitions={editForm.contactDefinitions}
            locale={data.locale}
            value={companyContacts}
            id="edit-company-contacts"
          />
        {:else}
          <!-- `?edit=1` can open this surface before the streamed lookups land (#78 + #290), so
               the field says it is coming rather than rendering an empty picker. -->
          <p class="text-sm text-text-muted">{t("common.loading")}</p>
        {/if}
      </CompanyForm>
      <div use:filedrop>
        <!-- Per-client logo (#196): shown on this page's header and on the client's portal
           dashboard. Not the agency's branding — that lives under Instellingen. -->
        <label for="edit-company-logo" class="mb-1 block text-sm font-medium text-text"
          >{t("companies.logo.label")}</label
        >
        <input
          id="edit-company-logo"
          name="logo_file"
          type="file"
          accept="image/png,image/jpeg,image/webp,image/gif"
          class="block w-full text-sm text-text-muted file:mr-3 file:cursor-pointer file:rounded-lg file:border file:border-solid file:border-border file:bg-transparent file:px-3 file:py-1.5 file:text-sm file:text-text hover:file:border-brand"
        />
        <p class="mt-1 text-xs text-text-muted">{t("common.drop_hint")}</p>
        {#if company.logo_file_id}
          <label class="mt-2 flex items-center gap-2 text-sm text-text">
            <input type="checkbox" name="logo_remove" value="1" />
            {t("companies.logo.remove")}
          </label>
        {/if}
        <p class="mt-1 text-xs text-text-muted">{t("companies.logo.hint")}</p>
      </div>
      {#if form?.error}<p class="text-sm text-red-600">{t(form.error)}</p>{/if}
      <div
        class="sticky bottom-0 -mx-4 flex justify-end gap-2 border-t border-border bg-surface-raised px-4 py-3"
      >
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm"
          onclick={() => (showEdit = false)}
        >
          {t("common.cancel")}
        </button>
        <Button loading={busy.is("update")} disabled={busy.active}>
          {t("common.save")}
        </Button>
      </div>
    </form>
  </SlideOver>
{/if}

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("common.delete")}
  message={t("companies.delete_confirm", { name: company.name })}
  action="?/delete"
/>
