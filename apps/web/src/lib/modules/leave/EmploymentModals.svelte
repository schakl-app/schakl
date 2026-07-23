<script lang="ts" module>
  import type { ActionItem } from "$lib/core/ui/ActionsMenu.svelte";
  import { BadgeEuro, CalendarClock, FileText, Repeat } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";

  /** The minimal member shape both `/members` and `/members/lookup` satisfy — a name and an id. */
  export type EmploymentMember = { user_id: string; full_name: string | null; email: string };
  export type EmploymentKind = "schedule" | "contracts" | "recurring" | "rate";
  /** Handed to a host via `register`; a ⋯ item calls it to open the right modal for a member. */
  export type OpenEmployment = (member: EmploymentMember, kind: EmploymentKind) => void;

  /**
   * The employment actions for one member's ⋯ menu, shared by Instellingen → Gebruikers and the
   * team leave roster so the two can't drift. An action appears only when its capability flag is
   * passed: the team roster omits the salary-adjacent `rate`, which stays a Gebruikers-only act.
   */
  export function employmentMenuItems(
    member: EmploymentMember,
    open: OpenEmployment | undefined,
    opts: { schedules: boolean; rates: boolean },
  ): ActionItem[] {
    const items: ActionItem[] = [];
    if (opts.schedules) {
      items.push({
        label: t("settings.users.schedule"),
        icon: CalendarClock,
        onclick: () => open?.(member, "schedule"),
      });
      items.push({
        label: t("settings.users.contracts"),
        icon: FileText,
        onclick: () => open?.(member, "contracts"),
      });
      items.push({
        label: t("settings.users.recurring"),
        icon: Repeat,
        onclick: () => open?.(member, "recurring"),
      });
    }
    if (opts.rates) {
      items.push({
        label: t("settings.users.rate"),
        icon: BadgeEuro,
        onclick: () => open?.(member, "rate"),
      });
    }
    return items;
  }
</script>

<script lang="ts">
  /**
   * Every employment-data editor for one member — work schedule (#46), contracts (#65),
   * recurring rostered-free days (#107) and hourly rate (#82) — as one shared surface. The
   * modals, their state and the "N days placed" line live here once; a host page mounts a single
   * instance, receives the `open(member, kind)` opener through `register`, and wires it onto each
   * row's ⋯ menu via {@link employmentMenuItems}. Both Instellingen → Gebruikers and the team
   * leave roster drive it, so the two can never drift.
   *
   * The forms post to `?/saveSchedule`, `?/saveContract`, `?/terminateContract`,
   * `?/deleteContract`, `?/saveRate` and the three `?/…Recurring` actions — every host declares
   * them by spreading `employmentActions` (employment.server.ts).
   */
  import { Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { fmtNumericDate } from "$lib/core/format";
  // `t` is imported in the module script above and is in scope here and in the markup.
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";

  import { fmtHours, type LeaveTypeInfo } from "./format";
  import RecurringDaysManager from "./RecurringDaysManager.svelte";
  import {
    cloneSchedule,
    defaultSchedule as defaultScheduleFn,
    weekHours,
    type WorkSchedule,
  } from "./schedule";
  import WorkScheduleEditor from "./WorkScheduleEditor.svelte";

  interface ProfileRow {
    user_id: string;
    // The generated client types weekdays as optional, so it is not a bare `WorkSchedule`;
    // `openSchedule` casts on read, exactly as the page did before this was shared (#46).
    schedule: unknown;
  }
  interface ContractRow {
    id: string;
    user_id: string;
    contract_hours_per_week: string | number;
    scheduled_hours_per_week: string | number;
    start_date: string;
    end_date: string | null;
  }
  interface RecurringRow {
    id: string;
    user_id: string;
    leave_type_id: string;
    anchor_date: string;
    interval_weeks: number;
    start_time?: string | null;
    end_time?: string | null;
    active: boolean;
  }
  /** Only the fields these modals read + the recurring "N placed for <name>" success line. */
  interface EmploymentForm {
    error?: string | null;
    recurringAdded?: boolean;
    recurringSaved?: boolean;
    recurringGenerated?: number;
  }

  let {
    register,
    profiles = [],
    contracts = [],
    recurring = [],
    leaveTypes = [],
    orgDefaultSchedule,
    rateByUser = {},
    canEditRates = false,
    form = null,
  }: {
    /** Called once with the opener so a host can trigger a modal from a ⋯ item. */
    register?: (open: OpenEmployment) => void;
    profiles?: ProfileRow[];
    contracts?: ContractRow[];
    recurring?: RecurringRow[];
    /** Active types the recurring planner may use (already the tenant's; filtered here to active). */
    leaveTypes?: LeaveTypeInfo[];
    /** The org default week the schedule editor inherits from (Instellingen → Verlof). */
    orgDefaultSchedule: WorkSchedule;
    /** Personal hourly rate per user; only passed where the caller may see rates (#82). */
    rateByUser?: Record<string, unknown>;
    canEditRates?: boolean;
    form?: EmploymentForm | null;
  } = $props();

  const busy = new InFlight();
  const todayIso = new Date().toISOString().slice(0, 10);

  // One member is targeted at a time; the ⋯ item that opened a modal chose it. The four modals
  // are a single instance each, so the returned `form` (page-level, no member id) still lands on
  // the right person — the same reason this lived in the page before it was shared.
  let member = $state<EmploymentMember | null>(null);
  let scheduleOpen = $state(false);
  let contractsOpen = $state(false);
  let recurringOpen = $state(false);
  let rateOpen = $state(false);

  const profileByUser = $derived(Object.fromEntries(profiles.map((p) => [p.user_id, p])));
  const contractsByUser = $derived.by(() => {
    const map: Record<string, ContractRow[]> = {};
    for (const c of contracts) (map[c.user_id] ??= []).push(c);
    return map;
  });
  const recurringByUser = $derived.by(() => {
    const map: Record<string, RecurringRow[]> = {};
    for (const p of recurring) (map[p.user_id] ??= []).push(p);
    return map;
  });
  const activeLeaveTypes = $derived(leaveTypes.filter((lt) => lt.active));

  // --- work schedule (#46) --------------------------------------------------------
  let inherit = $state(true);
  // Filled by `openSchedule` before the modal is ever shown; the initial value is never rendered.
  let draft = $state<WorkSchedule>(defaultScheduleFn());

  function openSchedule(target: EmploymentMember) {
    const own = profileByUser[target.user_id]?.schedule ?? null;
    inherit = own === null;
    draft = cloneSchedule((own ?? orgDefaultSchedule) as WorkSchedule);
    scheduleOpen = true;
  }

  // --- hourly rate (#82) ----------------------------------------------------------
  let rateDraft = $state("");
  function openRate(target: EmploymentMember) {
    const current = rateByUser[target.user_id];
    rateDraft = current == null ? "" : String(current);
    rateOpen = true;
  }

  // --- contract termination (#65) -------------------------------------------------
  // Asking *per which date* rather than assuming today: an open-ended ("doorlopend") contract can
  // be agreed to end on a specific future or past date; the row survives as history.
  let terminateOpen = $state(false);
  let terminateFor = $state<ContractRow | null>(null);
  let terminateDate = $state(todayIso);
  function openTerminate(contract: ContractRow) {
    terminateFor = contract;
    terminateDate = todayIso;
    terminateOpen = true;
  }

  const open: OpenEmployment = (target, kind) => {
    member = target;
    if (kind === "schedule") openSchedule(target);
    else if (kind === "contracts") contractsOpen = true;
    else if (kind === "recurring") recurringOpen = true;
    else openRate(target);
  };
  // The host stores this to trigger a modal from a row's ⋯ menu — a callback prop, not an
  // imperative ref, so it fits how this codebase wires shared surfaces (ondone, oncreate, …).
  $effect(() => register?.(open));

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<!-- The ADV modal closes on a successful add (#271), so its "N days placed" line lands here,
     where it outlives the surface that produced it. Named: a bare count would not say whose
     calendar just filled up. -->
{#if form?.recurringAdded && member}
  <p class="mb-4 text-sm text-green-600 dark:text-green-400">
    {t("settings.users.recurring_generated", {
      count: form.recurringGenerated ?? 0,
      name: member.full_name || member.email,
    })}
  </p>
{/if}

<!-- This person's working week (#46). One save; contract hours are derived from it. -->
<Modal bind:open={scheduleOpen} title={t("settings.users.schedule")}>
  {#if member}
    {#key member.user_id}
      <div class="space-y-4">
        <p class="text-sm text-text-muted">
          {member.full_name || member.email}
        </p>

        <label class="flex items-center gap-2 text-sm text-text">
          <input type="checkbox" bind:checked={inherit} class="h-4 w-4 rounded border-border" />
          {t("settings.users.schedule_inherit")}
        </label>

        {#if inherit}
          <p class="rounded-lg bg-surface px-3 py-2 text-xs text-text-muted">
            {t("settings.users.schedule_inherited_hint", {
              hours: fmtHours(weekHours(orgDefaultSchedule)),
            })}
          </p>
        {/if}

        <!-- Rendered outside the form on purpose: its TimeInputs post hidden fields of their own
             and a form they are not inside is a form they cannot pollute. -->
        <div class:opacity-50={inherit} class:pointer-events-none={inherit}>
          <WorkScheduleEditor
            bind:schedule={draft}
            formId="user-schedule-form"
            disabled={inherit}
          />
        </div>

        {#if form?.error}<p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>{/if}

        <form
          id="user-schedule-form"
          method="POST"
          action="?/saveSchedule"
          class="flex justify-end"
          use:enhance={busy.wrap("schedule", () => ({ result, update }) => {
            if (result.type === "success") scheduleOpen = false;
            void update({ reset: false });
          })}
        >
          <input type="hidden" name="user_id" value={member.user_id} />
          <input type="hidden" name="inherit" value={String(inherit)} />
          <Button loading={busy.is("schedule")}>
            {t("common.save")}
          </Button>
        </form>
      </div>
    {/key}
  {/if}
</Modal>

<!-- Employment contracts (#65): contract hours, distinct from scheduled hours; ADV accrues on
     the gap. A changed contract is a new row, so this is add + terminate, never edit-in-place. -->
<Modal bind:open={contractsOpen} title={t("settings.users.contracts")}>
  {#if member}
    {#key member.user_id}
      {@const rows = contractsByUser[member.user_id] ?? []}
      <div class="space-y-4">
        <p class="text-sm text-text-muted">{member.full_name || member.email}</p>

        {#if rows.length > 0}
          <ul class="divide-y divide-border rounded-lg border border-border">
            {#each rows as contract (contract.id)}
              <li class="flex items-center gap-3 px-3 py-2 text-sm">
                <div class="min-w-0 flex-1">
                  <span class="font-medium text-text">
                    {t("settings.users.contract_hours_value", {
                      hours: fmtHours(contract.contract_hours_per_week),
                    })}
                  </span>
                  <span class="block text-xs text-text-muted">
                    <!-- Through the shared formatter, so the period honors the personal
                         date-format preference like the rest of the app (#104). -->
                    {fmtNumericDate(contract.start_date)} → {contract.end_date
                      ? fmtNumericDate(contract.end_date)
                      : t("settings.users.contract_open")}
                    · {t("settings.users.contract_scheduled", {
                      hours: fmtHours(contract.scheduled_hours_per_week),
                    })}
                  </span>
                </div>
                {#if !contract.end_date}
                  <Button
                    variant="secondary"
                    size="xs"
                    type="button"
                    onclick={() => openTerminate(contract)}
                    title={t("settings.users.contract_terminate")}
                  >
                    {t("settings.users.contract_terminate")}
                  </Button>
                {/if}
                <form method="POST" action="?/deleteContract" use:enhance>
                  <input type="hidden" name="contract_id" value={contract.id} />
                  <button
                    class="rounded-lg p-1 text-text-muted hover:text-red-600 dark:hover:text-red-400"
                    title={t("common.delete")}
                    aria-label={t("common.delete")}
                  >
                    <Trash2 size={14} />
                  </button>
                </form>
              </li>
            {/each}
          </ul>
        {:else}
          <p class="rounded-lg bg-surface px-3 py-2 text-xs text-text-muted">
            {t("settings.users.contract_empty")}
          </p>
        {/if}

        <!-- Keyed on the row count: a successful add re-mounts the form, which is what clears
             the DateInputs — their display text is component state a form reset cannot reach. -->
        {#key rows.length}
          <form
            method="POST"
            action="?/saveContract"
            class="space-y-3 border-t border-border pt-4"
            use:enhance={busy.wrap("saveContract", () => ({ result, update }) => {
              if (result.type === "success") void update({ reset: true });
              else void update({ reset: false });
            })}
          >
            <input type="hidden" name="user_id" value={member.user_id} />
            <p class="text-xs font-semibold uppercase tracking-wide text-text-muted">
              {t("settings.users.contract_add")}
            </p>
            <div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <!-- Shared DateInput, never a native type="date": browsers render those after the
                 browser locale, ignoring the personal date-format preference (#104, docs/UX.md). -->
              <div>
                <label for="c-start" class="mb-1 block text-xs text-text-muted"
                  >{t("settings.users.contract_start")}</label
                >
                <DateInput id="c-start" name="start_date" required />
              </div>
              <div>
                <label for="c-end" class="mb-1 block text-xs text-text-muted"
                  >{t("settings.users.contract_end")}</label
                >
                <DateInput id="c-end" name="end_date" />
              </div>
              <div>
                <label for="c-hours" class="mb-1 block text-xs text-text-muted"
                  >{t("settings.users.contract_hours")}</label
                >
                <input
                  id="c-hours"
                  name="contract_hours_per_week"
                  inputmode="decimal"
                  required
                  placeholder="38"
                  class={inputClass}
                />
              </div>
            </div>
            <p class="text-xs text-text-muted">{t("settings.users.contract_hint")}</p>
            {#if form?.error}<p class="text-sm text-red-600 dark:text-red-400">
                {t(form.error)}
              </p>{/if}
            <div class="flex justify-end">
              <Button loading={busy.is("saveContract")}>
                {t("settings.users.contract_add")}
              </Button>
            </div>
          </form>
        {/key}
      </div>
    {/key}
  {/if}
</Modal>

<!-- Terminating an open-ended contract asks for the effective end date (per which date) rather
     than assuming today; the contract stays on file as history, only its end date is recorded.
     The API is the authority: it rejects an end before the start (errors.leave_end_before_start). -->
<Modal bind:open={terminateOpen} title={t("settings.users.contract_terminate")}>
  {#if terminateFor}
    {#key terminateFor.id}
      <form
        method="POST"
        action="?/terminateContract"
        class="space-y-4"
        use:enhance={busy.wrap("terminateContract", () => ({ result, update }) => {
          if (result.type === "success") terminateOpen = false;
          void update({ reset: false });
        })}
      >
        <input type="hidden" name="contract_id" value={terminateFor.id} />
        <p class="text-sm text-text-muted">{t("settings.users.contract_terminate_prompt")}</p>
        <div>
          <label for="terminate-date" class="mb-1 block text-sm font-medium text-text">
            {t("settings.users.contract_terminate_date")}
          </label>
          <DateInput id="terminate-date" name="end_date" bind:value={terminateDate} required />
          <p class="mt-1 text-xs text-text-muted">
            {t("settings.users.contract_terminate_hint", {
              start: fmtNumericDate(terminateFor.start_date),
            })}
          </p>
        </div>
        {#if form?.error}<p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>{/if}
        <div class="flex justify-end gap-2">
          <button
            type="button"
            class="rounded-lg border border-border px-4 py-2 text-sm text-text"
            onclick={() => (terminateOpen = false)}>{t("common.cancel")}</button
          >
          <Button variant="danger" loading={busy.is("terminateContract")} disabled={!terminateDate}>
            {t("settings.users.contract_terminate")}
          </Button>
        </div>
      </form>
    {/key}
  {/if}
</Modal>

<!-- Recurring rostered free days / ADV (#107): a schedule-derived pattern the generator lays
     onto the calendar as pre-approved, individually movable free days. Shared surface with
     the employee's own on /leave; here a manager may plan any active type. -->
<Modal bind:open={recurringOpen} title={t("settings.users.recurring")}>
  {#if member}
    {#key member.user_id}
      <div class="space-y-4">
        <p class="text-sm text-text-muted">{member.full_name || member.email}</p>
        <RecurringDaysManager
          patterns={recurringByUser[member.user_id] ?? []}
          types={activeLeaveTypes}
          userId={member.user_id}
          error={form?.error ?? null}
          generated={form?.recurringSaved && !form.recurringAdded
            ? (form.recurringGenerated ?? 0)
            : null}
          ondone={() => (recurringOpen = false)}
        />
      </div>
    {/key}
  {/if}
</Modal>

<!-- This person's hourly rate (#82). Salary-adjacent — its own permission gates edit. -->
<Modal bind:open={rateOpen} title={t("settings.users.rate")}>
  {#if member}
    {#key member.user_id}
      <form
        method="POST"
        action="?/saveRate"
        class="space-y-4"
        use:enhance={busy.wrap("rate", () => ({ result, update }) => {
          if (result.type === "success") rateOpen = false;
          void update({ reset: false });
        })}
      >
        <input type="hidden" name="user_id" value={member.user_id} />
        <p class="text-sm text-text-muted">{member.full_name || member.email}</p>
        <div>
          <label for="hourly_rate" class="mb-1 block text-sm font-medium text-text">
            {t("settings.users.rate_label")}
          </label>
          <input
            id="hourly_rate"
            name="hourly_rate"
            inputmode="decimal"
            bind:value={rateDraft}
            disabled={!canEditRates}
            placeholder={t("settings.users.rate_placeholder")}
            class={inputClass}
          />
          <p class="mt-1 text-xs text-text-muted">{t("settings.users.rate_hint")}</p>
        </div>
        {#if form?.error}<p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>{/if}
        {#if canEditRates}
          <div class="flex justify-end">
            <Button loading={busy.is("rate")}>
              {t("common.save")}
            </Button>
          </div>
        {/if}
      </form>
    {/key}
  {/if}
</Modal>
