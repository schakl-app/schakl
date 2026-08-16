<script lang="ts" module>
  import type { ActionItem } from "$lib/core/ui/ActionsMenu.svelte";
  import { BadgeEuro, Briefcase, CalendarClock } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";

  /** The minimal member shape both `/members` and `/members/lookup` satisfy — a name and an id. */
  export type EmploymentMember = {
    user_id: string;
    full_name: string | null;
    email: string | null;
  };
  export type EmploymentKind = "employment" | "rate" | "availability";
  /** Handed to a host via `register`; a ⋯ item calls it to open the right modal for a member. */
  export type OpenEmployment = (member: EmploymentMember, kind: EmploymentKind) => void;

  /**
   * The employment actions for one member's ⋯ menu, shared by Instellingen → Gebruikers and the
   * team leave roster so the two can't drift. An action appears only when its capability flag is
   * passed: the team roster omits the salary-adjacent `rate`, which stays a Gebruikers-only act.
   *
   * **One item, not three.** Werkrooster / Contracten / Terugkerende vrije tijd used to be three
   * separate entries, which is how the relationship between them became invisible: contract hours
   * only mean something against the week that is worked, and free days exist because the two
   * differ. They are one wizard now (see {@link EmploymentWizard}).
   */
  export function employmentMenuItems(
    member: EmploymentMember,
    open: OpenEmployment | undefined,
    opts: { schedules: boolean; rates: boolean; availability?: boolean },
  ): ActionItem[] {
    const items: ActionItem[] = [];
    if (opts.schedules) {
      items.push({
        label: t("settings.employment.title"),
        icon: Briefcase,
        onclick: () => open?.(member, "employment"),
      });
    }
    // Its own permission (`leave.availability.write:any`), not `leave.profile.manage`: the
    // contract is the agency's record and the exceptions on top of it are the person's own, so
    // a host that cannot manage contracts may still be the one keeping a freelancer's calendar.
    if (opts.availability) {
      items.push({
        label: t("leave.availability.title"),
        icon: CalendarClock,
        onclick: () => open?.(member, "availability"),
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
   * Every employment-data editor for one member, as one shared surface: the **Dienstverband**
   * wizard (contract + werkweek + vrije tijd, #46/#65/#107 merged) and the hourly rate (#82),
   * which stays separate because it has its own permission and is nobody's idea of "employment
   * terms you set up once".
   *
   * A host page mounts a single instance, receives the `open(member, kind)` opener through
   * `register`, and wires it onto each row's ⋯ menu via {@link employmentMenuItems}. Both
   * Instellingen → Gebruikers and the team leave roster drive it, so the two can never drift.
   *
   * The forms post to `?/saveEmployment`, `?/withdrawFreeTime`, `?/terminateContract`,
   * `?/deleteContract` and `?/saveRate` — every host declares them by spreading
   * `employmentActions` (employment.server.ts).
   */
  import { enhance } from "$app/forms";
  import { currencySymbol, fmtNumericDate } from "$lib/core/format";
  // `t` is imported in the module script above and is in scope here and in the markup.
  import { memberLabel } from "$lib/core/members";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";

  import AvailabilityManager from "./AvailabilityManager.svelte";
  import type { AvailabilityEntry } from "./availability";
  import EmploymentWizard, {
    type WizardContract,
    type WizardPattern,
    type WizardResult,
  } from "./EmploymentWizard.svelte";
  import { type LeaveTypeInfo } from "./format";
  import { type WorkSchedule } from "./schedule";

  let {
    register,
    contracts = [],
    recurring = [],
    availability = [],
    leaveTypes = [],
    orgDefaultSchedule,
    rateByUser = {},
    canEditRates = false,
    form = null,
  }: {
    /** Called once with the opener so a host can trigger a modal from a ⋯ item. */
    register?: (open: OpenEmployment) => void;
    contracts?: WizardContract[];
    recurring?: WizardPattern[];
    /** Everyone's availability exceptions in the host's read window; grouped per member here. */
    availability?: AvailabilityEntry[];
    /** Active types the free-time step may plan with. */
    leaveTypes?: LeaveTypeInfo[];
    /** The org default week — the full-time norm, and what an inheriting contract follows. */
    orgDefaultSchedule: WorkSchedule;
    /** Personal hourly rate per user; only passed where the caller may see rates (#82). */
    rateByUser?: Record<string, unknown>;
    canEditRates?: boolean;
    form?: WizardResult | null;
  } = $props();

  const busy = new InFlight();
  const todayIso = new Date().toISOString().slice(0, 10);

  // One member is targeted at a time; the ⋯ item that opened a modal chose it. Both modals are a
  // single instance, so the returned `form` (page-level, no member id) still lands on the right
  // person — the same reason this lived in the page before it was shared.
  let member = $state<EmploymentMember | null>(null);
  let employmentOpen = $state(false);
  let rateOpen = $state(false);
  let availabilityOpen = $state(false);
  // `form` survives until the next navigation, so a run's receipt would still be showing the next
  // time the wizard opens. Whatever `form` holds at open is marked stale; a genuinely new action
  // result is a new object and so reads as live.
  let staleForm = $state<WizardResult | null>(null);
  const liveForm = $derived(form && form !== staleForm ? form : null);

  const contractsByUser = $derived.by(() => {
    const map: Record<string, WizardContract[]> = {};
    for (const c of contracts) (map[c.user_id] ??= []).push(c);
    return map;
  });
  const recurringByUser = $derived.by(() => {
    const map: Record<string, WizardPattern[]> = {};
    for (const p of recurring) (map[p.user_id] ??= []).push(p);
    return map;
  });
  const availabilityByUser = $derived.by(() => {
    const map: Record<string, AvailabilityEntry[]> = {};
    for (const entry of availability) (map[entry.user_id] ??= []).push(entry);
    return map;
  });
  const activeLeaveTypes = $derived(leaveTypes.filter((lt) => lt.active));

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
  let terminateFor = $state<WizardContract | null>(null);
  let terminateDate = $state(todayIso);
  function openTerminate(contract: WizardContract) {
    terminateFor = contract;
    terminateDate = todayIso;
    terminateOpen = true;
  }

  const open: OpenEmployment = (target, kind) => {
    member = target;
    staleForm = form;
    if (kind === "rate") openRate(target);
    else if (kind === "availability") availabilityOpen = true;
    else employmentOpen = true;
  };
  // The host stores this to trigger a modal from a row's ⋯ menu — a callback prop, not an
  // imperative ref, so it fits how this codebase wires shared surfaces (ondone, oncreate, …).
  $effect(() => register?.(open));

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<!-- Contract, werkweek and vrije tijd in one flow. `2xl` because step 2 carries the seven-row
     schedule grid; the steps themselves keep each screen short. -->
<Modal bind:open={employmentOpen} title={t("settings.employment.title")} size="2xl">
  {#if member}
    <!-- Keyed on the member *and* on whether a save has landed: re-mounting is what rewinds the
         wizard to step 1 for the next person, and what clears a finished run's receipt. -->
    {#key `${member.user_id}:${liveForm?.employmentSaved ? "done" : "open"}`}
      <EmploymentWizard
        memberName={memberLabel(member)}
        userId={member.user_id}
        contracts={contractsByUser[member.user_id] ?? []}
        patterns={recurringByUser[member.user_id] ?? []}
        leaveTypes={activeLeaveTypes}
        {orgDefaultSchedule}
        form={liveForm}
        onterminate={openTerminate}
      />
    {/key}
  {/if}
</Modal>

<!-- One person's availability: the days on top of the week they were engaged under. The same
     component the freelancer opens on their own /leave page, so the two can't drift — only the
     `userId` differs, and the API demands `leave.availability.write:any` for somebody else's. -->
<Modal bind:open={availabilityOpen} title={t("leave.availability.title")} size="lg">
  {#if member}
    {#key member.user_id}
      <p class="mb-3 text-sm text-text-muted">{memberLabel(member)}</p>
      <AvailabilityManager
        entries={availabilityByUser[member.user_id] ?? []}
        userId={member.user_id}
        error={form?.error ?? null}
      />
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
        <p class="text-sm text-text-muted">{memberLabel(member)}</p>
        <div>
          <label for="hourly_rate" class="mb-1 block text-sm font-medium text-text">
            {t("settings.users.rate_label", { currency: currencySymbol() })}
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
