<script lang="ts">
  /**
   * What this connection will actually do, in sentences, derived from the settings above it.
   *
   * The reason this component exists rather than a paragraph of help text: a sync's settings are
   * eight controls whose *combination* is what matters, and no single control can say what the
   * combination means. "Uren: twee richtingen" plus "Conflicten: Timeon wint" plus "Gefactureerde
   * uren beschermen: aan" is a sentence about somebody's timesheet, and until it is written down
   * as one, the only way to find out what it means is to press the button.
   *
   * It is #305's rule — *show the constraint working* before you remove the control — applied to a
   * whole policy rather than one field. The dangerous settings stay available and the screen says,
   * before Save, what turning them on will do tonight.
   *
   * Every line names the *direction* of the consequence, never just the state: "wijzigingen hier
   * gaan niet terug naar Timeon" rather than "richting: ophalen", because the second reads as a
   * label and the first reads as a warning where it needs to.
   */
  import {
    AlertTriangle,
    ArrowDown,
    ArrowUp,
    ArrowUpDown,
    CalendarRange,
    Clock,
    Lock,
    MinusCircle,
    ShieldCheck,
  } from "@lucide/svelte";

  import { fmtClockTime, fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";

  import type { TimeonAccount } from "./types";

  let { account }: { account: TimeonAccount } = $props();

  type Line = {
    icon: typeof ArrowDown;
    text: string;
    /** `warn` for a line that says somebody's data may be overwritten. */
    tone?: "warn";
  };

  const directionIcon = {
    off: MinusCircle,
    pull: ArrowDown,
    push: ArrowUp,
    two_way: ArrowUpDown,
  } as const;

  /** The window this run would cover, as two dates rather than a number of days.
   *
   * "45 dagen" is not checkable and "16-04-2026 t/m 31-05-2026" is (#312's rule about a
   * comparison naming its own span).
   *
   * Computed here **only because this is a preview of an unsaved setting**, which no server can
   * answer. The run itself resolves its window server-side, in the org's timezone, and records it
   * on the run — which is the number a report prints. Rendering goes through `fmtNumericDate`, so
   * the two dates are formatted in the tenant's zone even though the arithmetic is UTC; at worst
   * the preview is a day out at the edges, and the run report is not. */
  const windowRange = $derived.by(() => {
    const nowMs = Date.now();
    const dayMs = 86_400_000;
    return {
      from: fmtNumericDate(
        new Date(nowMs - Math.max(1, account.window_days) * dayMs).toISOString(),
      ),
      to: fmtNumericDate(new Date(nowMs).toISOString()),
    };
  });

  const idle = $derived(account.hours_direction === "off" && account.projects_direction === "off");

  /**
   * The schedule as a sentence, resolved from what the form currently says (#388).
   *
   * It used to be one constant string — *"Elke nacht rond 04:20"* — which was the only place the
   * hardcoded cron was ever stated to a user, and which stayed true-looking after that cron
   * stopped firing (#387). Naming the **zone** is half the fix: 04:20 was UTC, so the sentence
   * was wrong by an hour or two for every tenant that read it, and differently in summer.
   */
  const whenAuto = $derived.by(() => {
    const at = fmtClockTime((account.auto_time ?? "04:20").slice(0, 5));
    const zone = account.timezone ?? "UTC";
    if (account.auto_frequency === "hourly") return t("timeon.plan.when_hourly");
    if (account.auto_frequency === "every_n_hours") {
      return t("timeon.plan.when_every_n_hours", { hours: account.auto_interval_hours ?? 4 });
    }
    if (account.auto_frequency === "weekdays") {
      return t("timeon.plan.when_weekdays", { time: at, zone });
    }
    return t("timeon.plan.when_daily", { time: at, zone });
  });

  const lines = $derived.by((): Line[] => {
    if (idle) return [];
    const out: Line[] = [];

    out.push({
      icon: Clock,
      text: account.auto_sync ? whenAuto : t("timeon.plan.when_manual"),
    });

    if (account.hours_direction !== "off") {
      out.push({
        icon: directionIcon[account.hours_direction],
        text: t(`timeon.plan.hours_${account.hours_direction}`),
      });
      out.push({
        icon: CalendarRange,
        text: t("timeon.plan.window", {
          days: account.window_days,
          from: windowRange.from,
          to: windowRange.to,
        }),
      });
      // Stated on the screen rather than left in a doc, because it is the one property of this
      // integration a user cannot infer: Timeon's hour rows carry no modified timestamp, so a
      // change outside the window is not "synced later", it is never noticed.
      out.push({ icon: AlertTriangle, text: t("timeon.plan.window_note") });
    }

    if (account.projects_direction !== "off") {
      out.push({
        icon: directionIcon[account.projects_direction],
        text: t(`timeon.plan.projects_${account.projects_direction}`),
      });
      if (account.create_missing_projects) {
        out.push({ icon: directionIcon.pull, text: t("timeon.plan.create_projects") });
      }
    }

    if (account.history_floor) {
      out.push({
        icon: Lock,
        text: t("timeon.plan.floor", { date: fmtNumericDate(account.history_floor) }),
      });
    }

    if (account.hours_direction === "pull" || account.hours_direction === "two_way") {
      out.push({
        icon: account.protect_invoiced ? ShieldCheck : AlertTriangle,
        text: account.protect_invoiced
          ? t("timeon.plan.protect_invoiced_on")
          : t("timeon.plan.protect_invoiced_off"),
        tone: account.protect_invoiced ? undefined : "warn",
      });
      if (account.protect_approved) {
        out.push({ icon: ShieldCheck, text: t("timeon.plan.protect_approved_on") });
      }
    }

    if (account.hours_direction === "two_way") {
      const key = `timeon.plan.conflict_${account.conflict_policy}`;
      out.push({
        icon: account.conflict_policy === "manual" ? ShieldCheck : AlertTriangle,
        text: t(key),
        tone: account.conflict_policy === "manual" ? undefined : "warn",
      });
    }

    if (account.push_approvals) {
      out.push({ icon: directionIcon.push, text: t("timeon.plan.push_approvals") });
    }

    return out;
  });
</script>

<div class="rounded-lg border border-border bg-surface-raised p-4">
  <h4 class="text-sm font-semibold text-text">{t("timeon.plan.title")}</h4>
  {#if idle}
    <p class="mt-2 text-sm text-text-muted">{t("timeon.plan.idle")}</p>
  {:else}
    <ul class="mt-2 space-y-1.5">
      {#each lines as line (line.text)}
        <li class="flex items-start gap-2 text-sm">
          <!-- The state rides in both the glyph and the words, so a line still reads correctly
               to somebody who cannot tell the two colours apart (the calendar-feed rule). -->
          <line.icon
            size={15}
            class={`mt-0.5 shrink-0 ${line.tone === "warn" ? "text-amber-600" : "text-text-muted"}`}
            aria-hidden="true"
          />
          <span class={line.tone === "warn" ? "text-amber-700 dark:text-amber-500" : "text-text"}>
            {line.text}
          </span>
        </li>
      {/each}
    </ul>
  {/if}
</div>
