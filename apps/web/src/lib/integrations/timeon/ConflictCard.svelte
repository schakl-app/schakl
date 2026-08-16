<script lang="ts">
  /**
   * One conflict: the same hour, changed on both sides, side by side, with three buttons.
   *
   * The whole design brief for this card is **settle it without opening anything else**. A queue
   * whose rows each need two other screens is a queue that stays full, so the card carries whose
   * hours these are, which client, and both versions of every field that differs.
   *
   * Only the fields that *differ* are drawn. A diff that also printed the six things both sides
   * agree on would bury the one that matters, and the API already sends only the difference.
   *
   * The three buttons are three real answers, and the third is not a lesser version of the other
   * two: **"mag verschillen"** is a decision, it is recorded, and the same divergence is never
   * offered again (#318). Without it the honest thing a person wants to do — leave two systems
   * deliberately out of step for one entry — has no expression, so they close the tab instead.
   */
  import { ArrowRight, Building2, Clock, User } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";

  import { clockOf, DIFF_ORDER, durationOf, type TimeonConflict } from "./types";

  let {
    conflict,
    busy,
    mayResolve,
  }: { conflict: TimeonConflict; busy: InFlight; mayResolve: boolean } = $props();

  const differences = $derived(
    (conflict.differences ?? {}) as Record<string, { local: unknown; remote: unknown }>,
  );
  /** Fixed reading order, never the object's own: a JSONB column has no key order (#373). */
  const fields = $derived(DIFF_ORDER.filter((field) => field in differences));

  const local = $derived((conflict.local_snapshot ?? {}) as Record<string, unknown>);

  /**
   * A compared value, in words.
   *
   * `8100` and `44100` are what the two systems store and neither is a thing to show a person
   * (#300's `totalUsers` lesson): minutes print as `2:15`, a start-of-day second as `12:15`, a
   * reference as the fact that there is one. `?` is the sentinel for a reference neither side has
   * paired, and it prints as *"niet gekoppeld"* rather than as a question mark.
   */
  function render(field: string, value: unknown): string {
    if (value === null || value === undefined || value === "") return t("timeon.diff.none");
    if (value === "?") return t("timeon.diff.unpaired");
    switch (field) {
      case "minutes":
        return durationOf(Number(value));
      case "start_seconds":
        return clockOf(Number(value));
      case "started_on":
        return fmtNumericDate(String(value));
      case "billable":
        return value ? t("timeon.diff.billable_yes") : t("timeon.diff.billable_no");
      case "project":
      case "company":
        return t("timeon.diff.set");
      default:
        return String(value);
    }
  }
</script>

<article class="rounded-lg border border-amber-300 bg-surface p-4 dark:border-amber-500/40">
  <header class="flex flex-wrap items-center gap-x-3 gap-y-1">
    <span class="flex items-center gap-1.5 text-sm font-medium text-text">
      <Clock size={15} class="shrink-0 text-text-muted" aria-hidden="true" />
      {local.started_on ? fmtNumericDate(String(local.started_on)) : "—"}
    </span>
    {#if conflict.user_name}
      <span class="flex items-center gap-1.5 text-sm text-text-muted">
        <User size={14} class="shrink-0" aria-hidden="true" />
        {conflict.user_name}
      </span>
    {/if}
    {#if conflict.company_name}
      <span class="flex items-center gap-1.5 text-sm text-text-muted">
        <Building2 size={14} class="shrink-0" aria-hidden="true" />
        {conflict.company_name}
      </span>
    {/if}
    {#if conflict.local_id}
      <a class="text-sm text-brand hover:underline" href={`/time?entry=${conflict.local_id}`}>
        {t("timeon.conflict.open_entry")}
      </a>
    {/if}
  </header>

  <div class="mt-3 overflow-x-auto">
    <table class="w-full min-w-[26rem] text-sm">
      <thead>
        <tr class="text-left text-xs text-text-muted">
          <th class="pb-1 pr-3 font-normal">{t("timeon.conflict.field")}</th>
          <th class="pb-1 pr-3 font-normal">{t("timeon.conflict.here")}</th>
          <th class="pb-1 font-normal">{t("timeon.conflict.there")}</th>
        </tr>
      </thead>
      <tbody>
        {#each fields as field (field)}
          <tr class="border-t border-border">
            <td class="py-1 pr-3 text-text-muted">{t(`timeon.field.${field}`)}</td>
            <td class="py-1 pr-3 text-text">{render(field, differences[field].local)}</td>
            <td class="py-1 text-text">{render(field, differences[field].remote)}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>

  {#if mayResolve}
    <div class="mt-3 flex flex-wrap gap-2">
      {#each [["kept_local", "keep_here"], ["kept_remote", "keep_there"], ["dismissed", "allow"]] as [resolution, label] (resolution)}
        <!-- Only hidden inputs, so there is nothing to blank: `wrap` is the honest choice, and
             `forms:check` agrees — it requires a decision only where the user typed. -->
        <form
          method="POST"
          action="?/resolve"
          use:enhance={busy.wrap(`${conflict.id}:${resolution}`)}
        >
          <input type="hidden" name="conflict_id" value={conflict.id} />
          <input type="hidden" name="resolution" value={resolution} />
          <Button
            type="submit"
            variant="secondary"
            size="sm"
            loading={busy.is(`${conflict.id}:${resolution}`)}
            disabled={busy.active}
          >
            {t(`timeon.conflict.${label}`)}
            {#if resolution !== "dismissed"}
              <ArrowRight size={14} aria-hidden="true" />
            {/if}
          </Button>
        </form>
      {/each}
    </div>
    <p class="mt-1.5 text-xs text-text-muted">{t("timeon.conflict.help")}</p>
  {/if}
</article>
