<script lang="ts">
  /**
   * The rules an agent works inside, for one advertiser.
   *
   * Every numeric field is three-state: empty means *inherit* — the agency's house value, or the
   * built-in where the agency has set none — and the hint beside it says what that resolves to.
   * A blank box that silently means "something else decides" is the shape #312 named: a setting
   * you cannot check is a setting nobody trusts.
   *
   * The term lists **add to** the agency's rather than replacing them, and the resolved list is
   * shown underneath, so what is actually enforced is on the same screen as what was typed.
   *
   * Business-licensed — see LICENSE.
   */
  import { ArrowLeft } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { InFlight } from "$lib/core/submit.svelte";
  import { t } from "$lib/core/i18n";
  import { returnHref } from "$lib/core/screen-position.svelte";

  let { data, form } = $props();

  const busy = new InFlight();

  const base = $derived(`/marketing/google-ads/${page.params.accountId}`);
  const resolved = $derived(data.policy.resolved as Record<string, unknown>);

  /** What the empty box resolves to, spelled out rather than left as the word "inherit". */
  function inherits(value: unknown): string {
    return value === null || value === undefined ? t("google_ads.policy.no_limit") : String(value);
  }
</script>

<a
  href={returnHref(base)}
  class="mb-3 inline-flex items-center gap-1 text-sm text-text-muted hover:text-text"
>
  <ArrowLeft size={14} aria-hidden="true" />
  {t("google_ads.policy.back")}
</a>

{#if form?.saved}
  <p class="mb-3 rounded-xl border border-border bg-surface-raised p-3 text-sm text-text">
    {t("common.saved")}
  </p>
{:else if form?.key}
  <p class="mb-3 rounded-xl border border-border bg-surface-raised p-3 text-sm text-text">
    {t(form.key)}
  </p>
{/if}

<!-- `busy.keep` because this edits an existing record: SvelteKit's default resets the form on
     success, which would blank every box the user just filled in. -->
<form method="POST" use:enhance={busy.keep("policy")}>
  <section class="mb-6 max-w-3xl rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="mb-1 text-sm font-semibold text-text">{t("google_ads.policy.protected.title")}</h2>
    <p class="mb-3 text-xs text-text-muted">{t("google_ads.policy.protected.help")}</p>
    <textarea
      name="protected_terms"
      rows="5"
      value={(data.policy.protected_terms ?? []).join("\n")}
      class="w-full rounded-lg border border-border bg-surface px-3 py-2 font-mono text-sm"
    ></textarea>
    {#if (resolved.protected_terms as string[])?.length}
      <p class="mt-2 text-xs text-text-muted">
        {t("google_ads.policy.effective", {
          list: (resolved.protected_terms as string[]).join(", "),
        })}
      </p>
    {/if}
  </section>

  <section class="mb-6 max-w-3xl rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="mb-1 text-sm font-semibold text-text">{t("google_ads.policy.exclude.title")}</h2>
    <p class="mb-3 text-xs text-text-muted">{t("google_ads.policy.exclude.help")}</p>
    <textarea
      name="always_exclude"
      rows="5"
      value={(data.policy.always_exclude ?? []).join("\n")}
      class="w-full rounded-lg border border-border bg-surface px-3 py-2 font-mono text-sm"
    ></textarea>
    {#if (resolved.always_exclude as string[])?.length}
      <p class="mt-2 text-xs text-text-muted">
        {t("google_ads.policy.effective", {
          list: (resolved.always_exclude as string[]).join(", "),
        })}
      </p>
    {/if}
  </section>

  <section class="mb-6 max-w-3xl rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="mb-1 text-sm font-semibold text-text">{t("google_ads.policy.limits.title")}</h2>
    <p class="mb-3 text-xs text-text-muted">{t("google_ads.policy.limits.help")}</p>
    <div class="grid gap-4 sm:grid-cols-2">
      {#each [["max_daily_budget", data.policy.max_daily_budget, resolved.max_daily_budget], ["max_budget_increase_pct", data.policy.max_budget_increase_pct, resolved.max_budget_increase], ["max_cpc", data.policy.max_cpc, resolved.max_cpc], ["waste_min_cost", data.policy.waste_min_cost, resolved.waste_min_cost], ["waste_min_clicks", data.policy.waste_min_clicks, resolved.waste_min_clicks]] as [name, own, effective] (name)}
        <label class="text-sm">
          <span class="mb-1 block text-xs font-medium text-text-muted">
            {t(`google_ads.policy.field.${name}`)}
          </span>
          <input
            name={String(name)}
            inputmode="decimal"
            value={own ?? ""}
            placeholder={t("google_ads.policy.inherit")}
            class="w-full rounded-lg border border-border bg-surface px-3 py-1.5 text-sm"
          />
          <span class="mt-1 block text-xs text-text-muted">
            {t("google_ads.policy.resolves_to", { value: inherits(effective) })}
          </span>
        </label>
      {/each}
    </div>
  </section>

  <section class="mb-6 max-w-3xl rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="mb-1 text-sm font-semibold text-text">{t("google_ads.policy.steering.title")}</h2>
    <p class="mb-3 text-xs text-text-muted">{t("google_ads.policy.steering.help")}</p>
    <textarea
      name="steering"
      rows="4"
      value={data.policy.steering}
      class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm"></textarea>
    {#if data.house?.steering}
      <!-- The agency's own paragraph, shown but not editable here and never merged into the box
           above: two claims of different kinds, kept apart all the way to the model (#300). -->
      <p class="mt-2 whitespace-pre-line text-xs text-text-muted">
        {t("google_ads.policy.house_steering")}: {data.house.steering}
      </p>
    {/if}
  </section>

  <section class="mb-6 max-w-3xl rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="mb-1 text-sm font-semibold text-text">{t("google_ads.policy.copy.title")}</h2>
    <p class="mb-3 text-xs text-text-muted">{t("google_ads.policy.copy.help")}</p>
    <textarea
      name="ad_copy_rules"
      rows="4"
      value={data.policy.ad_copy_rules}
      class="mb-3 w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm"></textarea>
    <span class="mb-1 block text-xs font-medium text-text-muted"
      >{t("google_ads.policy.field.banned_phrases")}</span
    >
    <textarea
      name="banned_phrases"
      rows="3"
      value={(data.policy.banned_phrases ?? []).join("\n")}
      class="w-full rounded-lg border border-border bg-surface px-3 py-2 font-mono text-sm"
    ></textarea>
  </section>

  <button type="submit" class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white"
    >{t("common.save")}</button
  >
</form>
