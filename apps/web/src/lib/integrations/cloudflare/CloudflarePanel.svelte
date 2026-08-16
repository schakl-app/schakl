<script lang="ts">
  /**
   * The Cloudflare panel on a domain's detail page (epic #278).
   *
   * Two states, and the difference matters: what the page loads is what schakl *stored* — no
   * Cloudflare call, so opening a domain is as fast as it was and still works when Cloudflare
   * is down (docs/PERFORMANCE.md). "Controleren bij Cloudflare" is the explicit action that
   * goes and looks, and it is the only thing that can answer *"this already redirects, but not
   * through us"*: a forwarding Page Rule, a redirect rule above ours, an apex with no proxied
   * record so the rule never fires.
   *
   * Every write control gates on the API's own key (docs/UX.md, the client-portal entry) —
   * `cloudflare.zone.manage`, base key. A domain page is client-reachable through the portal,
   * and none of this is a client's to touch.
   *
   * The zone decides what *this* Cloudflare account can be asked about the domain, so the
   * redirect and the DNS table live behind it. **Pages does not**: a custom hostname is
   * registered on a project, and the project names its own account (`docs/CLOUDFLARE.md` §6).
   * It therefore renders whether or not the domain is connected here.
   *
   * **Host contract:** `?/cfConnect`, `?/cfCheck`, `?/cfSaveRedirect`, `?/cfRemoveRedirect`,
   * `?/cfAdoptRedirect`, `?/cfEditRule`, `?/cfDeleteRule`, `?/cfLinkPages`, `?/cfUnlinkPages`
   * plus the DNS actions used by `CloudflareDns` (spread `cloudflareActions`).
   *
   * The redirect section lists **every** redirect this domain has, from whichever source, and
   * each row carries the three acts that apply to it: edit it at Cloudflare, delete it at
   * Cloudflare, and — for a rule schakl could have written — adopt it. That is what turns an
   * inherited redirect from a finding into a record. Editing and deleting name the rule by id and
   * do not claim it; adoption is the separate, explicit act of taking it on.
   */
  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";

  import CloudflareDns from "./CloudflareDns.svelte";
  import type { AccountOption, DomainStatus, PagesProject } from "./types";

  // `context` (the domain id) is part of the panel contract but unread here: every action is
  // the host page's own, so the domain comes from the route, never from a hidden field.
  let { data }: { data: unknown } = $props();

  const panel = $derived(
    (data ?? { status: null, projects: [], accounts: [] }) as {
      status: DomainStatus | null;
      projects: PagesProject[];
      accounts: AccountOption[];
    },
  );

  // A check returns its report to the page rather than through `load` (the load is deliberately
  // Cloudflare-free), so the freshest answer wins while the page lives.
  const live = $derived((page.form?.cfStatus ?? null) as DomainStatus | null);
  const status = $derived(live ?? panel.status);
  const canManage = $derived(can(page.data.user, "cloudflare.zone.manage"));

  const busy = new InFlight();
  let confirmRemoveRedirect = $state(false);
  let confirmDeleteRule = $state(false);
  let deleteRuleTarget = $state<{ id: string; label: string } | null>(null);
  let confirmUnlink = $state(false);
  let unlinkTarget = $state<{ id: string; hostname: string; project: string } | null>(null);

  const activeAccounts = $derived(panel.accounts.filter((a) => a.active));
  const zone = $derived(status?.zone ?? null);
  const redirect = $derived(status?.redirect ?? null);
  const issues = $derived(status?.issues ?? []);
  // Everything at Cloudflare that redirects and is not ours. Filled on a **stored** read too
  // (from the zone's last observation), which is what lets an inherited redirect be on the page
  // the moment it opens rather than only while a check's answer is on screen.
  const observed = $derived(status?.conflicts ?? []);
  const otherRules = $derived(observed.filter((row) => row.kind === "redirect_rule"));
  const pageRules = $derived(observed.filter((row) => row.kind === "page_rule"));
  // The API raises findings for an **unconnected** domain too: `domain_says_redirect` (this
  // record says it redirects and no rule of ours does, which is exactly how a redirect wired
  // outside schakl looks) and `duplicate_zone`. They were computed and then dropped, because
  // the issues box lived inside the connected branch — so the one state where a finding cannot
  // be discovered any other way was the one state that rendered none of them.
  // `not_connected` is dropped instead: the paragraph above already says it.
  const openIssues = $derived(issues.filter((issue) => issue !== "not_connected"));

  // What the page draws is what schakl stored, so the one thing it cannot leave unsaid is how
  // old that is: "no conflicts" from a check that ran in March is not the same sentence as
  // "no conflicts" from one that ran a minute ago, and without a date they read identically.
  // It sits with the button that changes it, in both branches that have one.
  const checked = $derived(
    status?.checked_at
      ? t("cloudflare.panel.checked_at", { when: fmtDateTime(status.checked_at) })
      : t("cloudflare.panel.never_checked"),
  );

  // The delegation verdict is tri-state: `null` means one of the two sides did not answer, which
  // is neither "delegated" nor an instruction to go and change nameservers at a registrar.
  const delegation = $derived.by(() => {
    if (status?.nameservers_delegated === true) {
      return { text: t("cloudflare.panel.delegated"), tone: "text-text-muted" };
    }
    if (status?.nameservers_delegated === false) {
      return { text: t("cloudflare.panel.not_delegated"), tone: "text-amber-600" };
    }
    return { text: t("cloudflare.panel.delegation_unknown"), tone: "text-text-muted" };
  });

  const inputClass =
    "w-full min-w-0 rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";
  const labelClass = "mb-1 block text-sm font-medium text-text";

  /** Cloudflare's own zone vocabulary, translated where we know it; raw where we don't. */
  function zoneStatus(value: string): string {
    const key = `cloudflare.zone_status.${value}`;
    const label = t(key);
    return label === key ? value : label;
  }

  const statusCodes = [301, 302, 307, 308];

  // The form is **collapsed until asked for**. A domain-wide redirect is a thing most domains
  // never have, and the panel opened with a target field, a status select and four checkboxes
  // permanently expanded over the top of it — which also meant a redirect that *did* exist was
  // only ever shown as a pre-filled form, never stated as a fact.
  let formOpen = $state(false);
  let target = $state("");
  let statusCode = $state(301);
  let preservePath = $state(true);
  let preserveQuery = $state(true);
  let includeSubdomains = $state(true);
  // Which rule the open form writes. `null` = schakl's own redirect, through `?/cfSaveRedirect`,
  // which may also *create* one. A rule id means an existing rule at Cloudflare, edited in place
  // through `?/cfEditRule` — a different endpoint because it is a different act: it cannot create
  // anything, and editing a rule schakl does not own deliberately does not claim it.
  let editRuleId = $state<string | null>(null);
  // Whether this rule's match set is one schakl can rewrite (the API's tri-state
  // `include_subdomains`). False means the expression is kept verbatim, so the checkbox is not
  // drawn — a control that silently does nothing is worse than one that is absent, and here the
  // *absence* is the honest statement that the rule's reach is not being touched.
  let scopeEditable = $state(true);
  // The code the rule holds when it is one schakl cannot write (a 303, say). The select offers the
  // four we can express, so such a rule cannot keep its code through an edit — and a `<select>`
  // whose bound value matches no option renders blank and posts nothing, which would have turned
  // it into a 301 with no word said. Named here so the form can say it out loud instead.
  let unwritableCode = $state<number | null>(null);

  /** Seed the form from an intent (editing) or from nothing (adding), and open it. */
  function openForm(intent?: {
    target_url?: string | null;
    status_code?: number | null;
    preserve_path?: boolean | null;
    preserve_query?: boolean | null;
    include_subdomains?: boolean | null;
  }) {
    // Falls back to what the *domain record* says it redirects to. That is the state an agency
    // inherits — the domain is marked "omleiding" here and the rule was made in Cloudflare's
    // dashboard — so an empty box there means retyping a URL both sides already know.
    target = intent?.target_url ?? status?.domain_redirect_url ?? "";
    statusCode = intent?.status_code ?? 301;
    preservePath = intent?.preserve_path ?? true;
    preserveQuery = intent?.preserve_query ?? true;
    includeSubdomains = intent?.include_subdomains ?? true;
    editRuleId = null;
    scopeEditable = true;
    unwritableCode = null;
    formOpen = true;
  }

  /**
   * Open the form over a rule that already exists at Cloudflare.
   *
   * Seeded **field by field from the rule's own settings**, never from `intent`. `intent` is
   * all-or-nothing by design — it answers "could schakl have written this whole rule?" — so one
   * unreadable part of a rule would fill every other field with a *default*, and saving would
   * write those defaults back: a 303 quietly becomes a 301, a rule sending every URL to one page
   * starts appending paths, a redirect for one hostname widens to every subdomain of it. Three
   * silent changes to what a visitor's browser does, from pressing "Bewerken" and "Opslaan".
   *
   * That is not a corner case: Cloudflare's own dashboard writes
   * `http.host in {"klant.nl" "www.klant.nl"}`, which we read and cannot reproduce, so the
   * commonest inherited redirect is exactly the one with no whole intent.
   */
  function openRuleForm(rule: {
    rule_id?: string | null;
    target_url?: string | null;
    include_subdomains?: boolean | null;
    preserve_path?: boolean | null;
    preserve_query?: boolean | null;
    status_code?: number | null;
  }) {
    target = rule.target_url ?? "";
    // A code outside the four is kept out of the select (which would render blank and post
    // nothing) and stated instead, so changing it is something the user is told about.
    unwritableCode =
      rule.status_code != null && !statusCodes.includes(rule.status_code) ? rule.status_code : null;
    statusCode = unwritableCode === null ? (rule.status_code ?? 301) : 301;
    preservePath = rule.preserve_path ?? true;
    preserveQuery = rule.preserve_query ?? true;
    // `null` is "this rule's match set is not ours to rewrite", which is a different statement
    // from `false` and the reason the checkbox is dropped rather than drawn unticked.
    scopeEditable = rule.include_subdomains !== null && rule.include_subdomains !== undefined;
    includeSubdomains = rule.include_subdomains ?? true;
    editRuleId = rule.rule_id ?? null;
    formOpen = true;
  }

  /** "301 · pad meenemen · incl. subdomeinen" — a row describing itself in the form's own words. */
  function summarise(intent: {
    status_code: number;
    preserve_path: boolean;
    preserve_query: boolean;
    include_subdomains: boolean;
  }): string {
    return [
      t(`cloudflare.redirect.code_${intent.status_code}`),
      intent.preserve_path ? t("cloudflare.redirect.preserve_path") : null,
      intent.preserve_query ? t("cloudflare.redirect.preserve_query") : null,
      t(
        intent.include_subdomains
          ? "cloudflare.redirect.scope_all"
          : "cloudflare.redirect.scope_apex",
      ),
    ]
      .filter(Boolean)
      .join(" · ");
  }

  // How old the redirect list is. Its own line rather than the panel's `checked_at`, because
  // this half has its own token scope and its own probe: it can be stale, or never read at all,
  // while the rest of the report is a minute old.
  const observedAt = $derived(
    status?.redirects_observed_at
      ? t("cloudflare.redirect.observed_at", { when: fmtDateTime(status.redirects_observed_at) })
      : t("cloudflare.redirect.never_observed"),
  );
</script>

{#if page.form?.cfError}
  <p class="mb-3 text-sm text-red-600 dark:text-red-400">{t(page.form.cfError)}</p>
{/if}

{#if !zone}
  <!-- Not connected. -->
  <p class="text-sm text-text-muted">{t("cloudflare.panel.not_connected")}</p>
  <!-- What the API found anyway. A domain marked "redirect" here with nothing behind it at
       Cloudflare is the #96 webhook-era state this module exists to replace, and it is only
       visible while the domain is unconnected — which is precisely when this box used not to
       render at all. -->
  {#if openIssues.length > 0}
    <div class="mt-3 rounded-lg border border-amber-300 bg-amber-50 p-3 dark:bg-amber-950/30">
      <p class="mb-1 text-xs font-medium text-text">{t("cloudflare.issues.title")}</p>
      <ul class="list-inside list-disc space-y-1 text-sm text-text">
        {#each openIssues as issue (issue)}
          <li>{t(`cloudflare.issue.${issue}`)}</li>
        {/each}
      </ul>
    </div>
  {/if}
  {#if canManage}
    {#if activeAccounts.length === 0}
      <p class="mt-2 text-sm text-text-muted">{t("cloudflare.issue.no_account")}</p>
    {:else}
      <form
        method="POST"
        action="?/cfConnect"
        use:enhance={busy.clear("connect")}
        class="mt-3 space-y-3"
      >
        {#if activeAccounts.length > 1}
          <div class="max-w-sm">
            <label class={labelClass} for="cf-account">{t("cloudflare.panel.account")}</label>
            <select id="cf-account" name="account_id" class={inputClass}>
              {#each activeAccounts as account (account.id)}
                <option value={account.id}>{account.name}</option>
              {/each}
            </select>
          </div>
        {/if}
        <label class="flex items-center gap-2 text-sm text-text">
          <input type="checkbox" name="adopt_only" class="rounded border-border" />
          {t("cloudflare.panel.adopt_only")}
        </label>
        <p class="text-xs text-text-muted">{t("cloudflare.panel.connect_help")}</p>
        <Button type="submit" loading={busy.is("connect")} disabled={busy.active}>
          {t("cloudflare.panel.connect")}
        </Button>
      </form>
    {/if}
  {/if}
{:else}
  <!-- Connected: the zone, its delegation, and what a check last found. -->
  <div class="flex flex-wrap items-baseline justify-between gap-2">
    <div class="min-w-0">
      <p class="truncate text-sm font-medium text-text">{zone.name}</p>
      <p class="text-xs text-text-muted">
        {zone.account_name ?? ""} · {zoneStatus(zone.status)}
      </p>
    </div>
    <div class="flex flex-none flex-col items-end gap-1">
      <form method="POST" action="?/cfCheck" use:enhance={busy.wrap("check")}>
        <Button variant="secondary" size="xs" loading={busy.is("check")} disabled={busy.active}>
          {t("cloudflare.panel.check")}
        </Button>
      </form>
      <p class="text-xs text-text-muted">{checked}</p>
    </div>
  </div>

  <dl class="mt-3 grid gap-3 text-sm sm:grid-cols-2">
    <div class="min-w-0">
      <dt class="text-xs text-text-muted">{t("cloudflare.panel.expected_nameservers")}</dt>
      <dd class="break-words text-text">
        {status?.expected_nameservers?.join(", ") || "—"}
      </dd>
    </div>
    <div class="min-w-0">
      <dt class="text-xs text-text-muted">{t("cloudflare.panel.observed_nameservers")}</dt>
      <dd class="break-words text-text">
        {status?.observed_nameservers?.join(", ") || "—"}
      </dd>
    </div>
  </dl>
  <!-- Three states, not two. `null` is "one of the two sides did not answer" — Cloudflare has
       assigned no nameservers yet, or the public-DNS lookup came back empty (which a timeout
       does, indistinguishably from a domain that really delegates nowhere). Rendered as a
       definite "not delegated" it told an agency to go and change something at the registrar
       that was very possibly already right. -->
  <p class="mt-1 text-xs {delegation.tone}">{delegation.text}</p>
  {#if status?.nameservers_checked_at}
    <!-- The observed half's own age. The check button refreshes it where the caller may, so
         without this line a stale reading and a fresh one look identical — the same argument
         `checked_at` exists for, applied to the side `checked_at` does not cover. -->
    <p class="text-xs text-text-muted">
      {t("cloudflare.panel.nameservers_checked_at", {
        when: fmtDateTime(status.nameservers_checked_at),
      })}
    </p>
  {/if}

  {#if issues.length > 0}
    <div class="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-3 dark:bg-amber-950/30">
      <p class="mb-1 text-xs font-medium text-text">{t("cloudflare.issues.title")}</p>
      <ul class="list-inside list-disc space-y-1 text-sm text-text">
        {#each issues as issue (issue)}
          <li>{t(`cloudflare.issue.${issue}`)}</li>
        {/each}
      </ul>
      {#if status?.unavailable?.length}
        <p class="mt-2 text-xs text-text-muted">
          {t("cloudflare.unavailable.title", {
            items: status.unavailable.map((k) => t(`cloudflare.unavailable.${k}`)).join(", "),
          })}
        </p>
      {/if}
    </div>
  {/if}

  <!-- Redirects ---------------------------------------------------------------------------
       A list of what this domain redirects *through*, from whichever source, and then one
       collapsed control to add another. Before this the section was a permanently-open form:
       a redirect schakl managed was only ever a pre-filled box, and a redirect Cloudflare
       already had appeared under "Aandachtspunten" as a conflict — the state an agency inherits,
       rendered as a fault, next to an empty form suggesting they make a second one. -->
  <section class="mt-5 border-t border-border pt-4">
    <div class="flex flex-wrap items-baseline justify-between gap-2">
      <h3 class="text-sm font-medium text-text">{t("cloudflare.redirect.title")}</h3>
      <span class="text-xs text-text-muted">{observedAt}</span>
    </div>

    <!-- The old amber "Andere omleidingen op deze zone" box is gone: its rows are the list
         below, where they belong — with none of ours they *are* this domain's redirects, and
         boxing them in a warning taught people to ignore the box. What survives is the one
         sentence that is only true when we hold a rule too (Cloudflare evaluates the ruleset
         top-down, so one of theirs can beat ours), and the API now raises `redirect_conflict`
         in exactly that case. It sits above the list it describes. -->
    {#if issues.includes("redirect_conflict")}
      <p class="mt-1 text-xs text-amber-600">{t("cloudflare.conflicts.intro")}</p>
    {/if}

    <!-- The rule is live and something *after* it was refused — today only the origin
         placeholder, whose scope is DNS rather than redirects. Cloudflare's own text, because
         "which permission is missing" is a sentence only Cloudflare can write and an i18n key
         cannot (§9: it never goes in the error envelope, it goes on the row). Without it the
         whole save used to fail, and the rule it had already created was invisible here. -->
    {#if redirect?.last_error}
      <p class="mt-2 text-sm text-amber-600">
        {t("cloudflare.redirect.origin_failed")}
        <span class="block break-words text-xs text-text-muted">{redirect.last_error}</span>
      </p>
    {/if}

    {#if redirect && status?.redirect_live?.differences?.length}
      <p class="mt-2 text-sm text-amber-600">
        {t("cloudflare.redirect.drift_explain", {
          fields: status.redirect_live.differences.join(", "),
        })}
      </p>
      {#if status.redirect_live.target}
        <p class="mt-1 break-words text-xs text-text-muted">
          {t("cloudflare.redirect.live_target")}: {status.redirect_live.target}
        </p>
      {/if}
    {/if}

    <!-- The list. One row per redirect that exists, ours first, each saying where it came
         from — because "beheerd via schakl" and "gevonden bij Cloudflare" are the difference
         between a rule this panel may edit and one it may only claim. -->
    {#if redirect || otherRules.length > 0 || pageRules.length > 0}
      <ul class="mt-3 divide-y divide-border border-y border-border">
        {#if redirect}
          <li class="flex flex-wrap items-start justify-between gap-2 py-2">
            <div class="min-w-0">
              <p class="break-words text-sm text-text">
                {status?.domain_name} → {redirect.target_url}
              </p>
              <p class="text-xs text-text-muted">{summarise(redirect)}</p>
              <p class="text-xs text-text-muted">
                {t("cloudflare.redirect.source_managed")} ·
                {t(`cloudflare.redirect_status.${redirect.last_status}`)}
              </p>
            </div>
            {#if canManage}
              <div class="flex flex-none gap-2">
                <Button
                  type="button"
                  variant="secondary"
                  size="xs"
                  onclick={() => openForm(redirect)}
                >
                  {t("cloudflare.redirect.edit")}
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  size="xs"
                  onclick={() => (confirmRemoveRedirect = true)}
                >
                  {t("cloudflare.redirect.remove")}
                </Button>
              </div>
            {/if}
          </li>
        {/if}

        {#each otherRules as rule, i (rule.rule_id ?? i)}
          <li class="flex flex-wrap items-start justify-between gap-2 py-2">
            <div class="min-w-0">
              <p class="break-words text-sm text-text">
                {#if rule.target_url}
                  {status?.domain_name} → {rule.target_url}
                {:else}
                  {rule.description || t("cloudflare.conflicts.redirect_rule")}
                {/if}
              </p>
              {#if rule.intent}
                <p class="text-xs text-text-muted">{summarise(rule.intent)}</p>
              {:else}
                <!-- Described by Cloudflare's own text, and no adopt button: a shape schakl
                     cannot express is one it must not claim to manage (#253). -->
                <p class="break-words text-xs text-text-muted">{rule.detail || rule.description}</p>
                <p class="text-xs text-text-muted">{t("cloudflare.redirect.unreadable")}</p>
              {/if}
              <p class="text-xs text-text-muted">
                {t("cloudflare.redirect.source_observed")}
                {#if rule.domain_wide}· {t("cloudflare.redirect.whole_domain")}{/if}
              </p>
            </div>
            <!-- The row's own controls. Edit and delete act on the rule **by id at Cloudflare**,
                 which is what makes an inherited redirect a record rather than a finding: before
                 them, correcting a URL somebody typed into Cloudflare's dashboard years ago meant
                 going back to Cloudflare's dashboard. Adopt is the third and quite separate act —
                 claiming the rule as schakl's — so it stays its own button. -->
            {#if canManage && rule.rule_id}
              <div class="flex flex-none flex-wrap gap-2">
                <!-- Editable as soon as we can read *where it goes*. Deliberately not gated on
                     `intent`: the commonest inherited shape (Cloudflare's own
                     `http.host in {…}`) has no whole intent and a perfectly readable target, and
                     gating on the intent would have withheld the button from exactly the rules
                     this feature exists for. What we cannot rewrite is the match set, and that
                     is handled by keeping it — see `openRuleForm`. -->
                {#if rule.target_url}
                  <Button
                    type="button"
                    variant="secondary"
                    size="xs"
                    onclick={() => openRuleForm(rule)}
                  >
                    {t("cloudflare.redirect.edit")}
                  </Button>
                {/if}
                <!-- Offered whether or not we can read the rule, because deleting needs no
                     understanding of its shape — and the one rule nobody can describe is the one
                     an agency most wants gone. Confirmed against Cloudflare's own text for it. -->
                <Button
                  type="button"
                  variant="secondary"
                  size="xs"
                  onclick={() => {
                    deleteRuleTarget = {
                      id: rule.rule_id ?? "",
                      label: rule.target_url || rule.description || rule.detail || "",
                    };
                    confirmDeleteRule = true;
                  }}
                >
                  {t("cloudflare.redirect.delete")}
                </Button>
                <!-- Adopting takes the rule by id and writes nothing at Cloudflare, so what a
                     visitor's browser does cannot change as a side effect of schakl taking
                     ownership. It posts **the rule's own** intent — before that it posted whatever
                     was typed in the form above, so the obvious press answered
                     `cloudflare_redirect_differs` until the admin hand-matched five fields to a
                     rule they could not see. Offered only where it can succeed: a rule we already
                     own is not adoptable. -->
                {#if rule.intent && (!redirect || status?.redirect_live?.present === false)}
                  <form
                    method="POST"
                    action="?/cfAdoptRedirect"
                    use:enhance={busy.keep(`adopt-${rule.rule_id}`)}
                  >
                    <input type="hidden" name="rule_id" value={rule.rule_id} />
                    <input type="hidden" name="target_url" value={rule.intent.target_url} />
                    <input type="hidden" name="status_code" value={rule.intent.status_code} />
                    {#if rule.intent.preserve_path}
                      <input type="hidden" name="preserve_path" value="on" />
                    {/if}
                    {#if rule.intent.preserve_query}
                      <input type="hidden" name="preserve_query" value="on" />
                    {/if}
                    {#if rule.intent.include_subdomains}
                      <input type="hidden" name="include_subdomains" value="on" />
                    {/if}
                    <Button
                      type="submit"
                      variant="secondary"
                      size="xs"
                      loading={busy.is(`adopt-${rule.rule_id}`)}
                      disabled={busy.active}
                    >
                      {t("cloudflare.conflicts.adopt")}
                    </Button>
                  </form>
                {/if}
              </div>
            {/if}
          </li>
        {/each}

        {#each pageRules as rule, i (i)}
          <li class="py-2">
            <p class="break-words text-sm text-text">{rule.description || "—"}</p>
            <p class="text-xs text-text-muted">
              {t("cloudflare.redirect.source_page_rule")}
              {#if rule.detail}· {rule.detail}{/if}
            </p>
          </li>
        {/each}
      </ul>
    {:else}
      <p class="mt-2 text-sm text-text-muted">{t("cloudflare.redirect.empty")}</p>
    {/if}

    {#if canManage && !formOpen}
      <button
        type="button"
        class="mt-3 text-sm text-brand hover:underline"
        onclick={() => openForm()}
      >
        ＋ {t("cloudflare.redirect.add")}
      </button>
    {/if}

    {#if canManage && formOpen}
      <form
        method="POST"
        action={editRuleId ? "?/cfEditRule" : "?/cfSaveRedirect"}
        use:enhance={busy.keep("redirect")}
        class="mt-3 space-y-3"
      >
        {#if editRuleId}
          <!-- The rule this form writes. Editing an existing rule is a different endpoint from
               saving schakl's own: it names a rule by id, cannot create one, and does not claim
               ownership of a rule it did not own. -->
          <input type="hidden" name="rule_id" value={editRuleId} />
          <p class="text-xs text-text-muted">{t("cloudflare.redirect.edit_rule_help")}</p>
        {/if}
        <div class="grid gap-3 sm:grid-cols-2">
          <div class="min-w-0">
            <label class={labelClass} for="cf-target">{t("cloudflare.redirect.target")}</label>
            <input
              id="cf-target"
              name="target_url"
              bind:value={target}
              placeholder={t("cloudflare.redirect.target_placeholder")}
              class={inputClass}
            />
          </div>
          <div class="min-w-0">
            <label class={labelClass} for="cf-code">{t("cloudflare.redirect.status_code")}</label>
            <select id="cf-code" name="status_code" bind:value={statusCode} class={inputClass}>
              {#each statusCodes as code (code)}
                <option value={code}>{t(`cloudflare.redirect.code_${code}`)}</option>
              {/each}
            </select>
            {#if unwritableCode !== null}
              <p class="mt-1 text-xs text-amber-600">
                {t("cloudflare.redirect.code_replaced", { code: unwritableCode })}
              </p>
            {/if}
            {#if statusCode === 301 || statusCode === 308}
              <p class="mt-1 text-xs text-text-muted">
                {t("cloudflare.redirect.code_permanent_warning")}
              </p>
            {/if}
          </div>
        </div>

        <label class="flex items-start gap-2 text-sm text-text">
          <input
            type="checkbox"
            name="preserve_path"
            bind:checked={preservePath}
            class="mt-0.5 rounded border-border"
          />
          <span>
            {t("cloudflare.redirect.preserve_path")}
            <span class="block text-xs text-text-muted">
              {t("cloudflare.redirect.preserve_path_help")}
            </span>
          </span>
        </label>
        <label class="flex items-center gap-2 text-sm text-text">
          <input
            type="checkbox"
            name="preserve_query"
            bind:checked={preserveQuery}
            class="rounded border-border"
          />
          {t("cloudflare.redirect.preserve_query")}
        </label>
        {#if scopeEditable}
          <label class="flex items-center gap-2 text-sm text-text">
            <input
              type="checkbox"
              name="include_subdomains"
              bind:checked={includeSubdomains}
              class="rounded border-border"
            />
            {t("cloudflare.redirect.include_subdomains")}
          </label>
        {:else}
          <!-- Not a checkbox that would be ignored: this rule's match set is one schakl can read
               and not reproduce, so the API keeps it verbatim and the edit moves the destination
               only. Saying so is the whole point — a drawn control that silently did nothing
               would be the same bug in a friendlier costume. -->
          <p class="text-xs text-text-muted">{t("cloudflare.redirect.keeps_match")}</p>
        {/if}
        {#if !editRuleId}
          <!-- Only where a rule may be *created*. A rule already in the ruleset is one traffic
               already reaches, so offering to add the placeholder that makes a new rule fire
               would be a checkbox with nothing to do. -->
          <label class="flex items-start gap-2 text-sm text-text">
            <input
              type="checkbox"
              name="ensure_origin"
              checked
              class="mt-0.5 rounded border-border"
            />
            <span>
              {t("cloudflare.redirect.ensure_origin")}
              <span class="block text-xs text-text-muted">
                {t("cloudflare.redirect.ensure_origin_help")}
              </span>
            </span>
          </label>
        {/if}

        <div class="flex flex-wrap items-center gap-2">
          <Button type="submit" loading={busy.is("redirect")} disabled={busy.active}>
            {editRuleId ? t("cloudflare.redirect.save_rule") : t("cloudflare.redirect.save")}
          </Button>
          <!-- Removing lives on the row it removes, not down here: this form is now reached by
               pressing "Bewerken" *on* that row, and a delete button at the bottom of an open
               form is one mis-click away from the save it sits next to. -->
          <Button type="button" variant="secondary" size="sm" onclick={() => (formOpen = false)}>
            {t("cloudflare.redirect.cancel")}
          </Button>
        </div>
      </form>
    {/if}
  </section>

  <!-- DNS ------------------------------------------------------------------------------- -->
  <CloudflareDns zoneId={zone.id} zoneName={zone.name} {canManage} />
{/if}

<!-- Cloudflare Pages -----------------------------------------------------------------------
     Outside the zone branch on purpose. A Pages custom hostname is registered on a *project*,
     which is an account-level thing: the API resolves the account from the project and only
     writes the CNAME when this domain happens to have a zone here. Drawn inside the connected
     branch, the feature read as "you cannot serve this domain from Pages" for every domain
     whose DNS lives elsewhere — and hid the links of a domain whose zone was later unlinked,
     leaving rows nothing on this page could remove. -->
{#if zone || status?.pages_links?.length || (canManage && panel.projects.length > 0)}
  <section class="mt-5 border-t border-border pt-4">
    <div class="flex flex-wrap items-baseline justify-between gap-2">
      <h3 class="text-sm font-medium text-text">{t("cloudflare.pages.title")}</h3>
      <!-- The zone branch has its own "check" button, and a domain whose DNS lives elsewhere
           is not inside it — so without this the one case Pages exists for could never
           refresh. The action is the same one; only the button is duplicated. -->
      {#if !zone && status?.pages_links?.length}
        <div class="flex flex-none flex-col items-end gap-1">
          <form method="POST" action="?/cfCheck" use:enhance={busy.wrap("check")}>
            <Button variant="secondary" size="xs" loading={busy.is("check")} disabled={busy.active}>
              {t("cloudflare.pages.check")}
            </Button>
          </form>
          <p class="text-xs text-text-muted">{checked}</p>
        </div>
      {/if}
    </div>
    {#if status?.pages_links?.length}
      <ul class="mt-2 space-y-1 text-sm">
        {#each status.pages_links as link (link.id)}
          <li class="flex flex-wrap items-center justify-between gap-2">
            <span class="min-w-0 break-words text-text">
              {link.hostname}
              <span class="text-text-muted">→ {link.project_name ?? ""}</span>
              {#if link.status}<span class="text-xs text-text-muted">({link.status})</span>{/if}
              <!-- Drift, and where the row came from. A link the sync adopted is one nobody
                   here created, so saying so is the difference between "who added this?" and
                   a row that looks like somebody's mistake. -->
              {#if link.missing_at}
                <span class="block text-xs text-amber-600">
                  {t("cloudflare.pages.missing", { when: fmtDateTime(link.missing_at) })}
                </span>
              {:else if link.discovered_at}
                <span class="block text-xs text-text-muted">
                  {t("cloudflare.pages.discovered")}
                </span>
              {/if}
            </span>
            {#if canManage}
              <Button
                type="button"
                variant="secondary"
                size="xs"
                onclick={() => {
                  unlinkTarget = {
                    id: link.id,
                    hostname: link.hostname,
                    project: link.project_name ?? "",
                  };
                  confirmUnlink = true;
                }}
              >
                {t("cloudflare.pages.unlink")}
              </Button>
            {/if}
          </li>
        {/each}
      </ul>
    {:else}
      <p class="mt-2 text-sm text-text-muted">{t("cloudflare.pages.empty")}</p>
    {/if}

    <!-- The issues box lives inside the connected branch, so a domain with no zone would get no
         word at all that the refresh could not run. A check that silently did nothing reads as
         "everything is fine", which is the one thing it does not know. -->
    {#if status?.unavailable?.includes("pages")}
      <p class="mt-2 text-xs text-amber-600">
        {t("cloudflare.unavailable.title", { items: t("cloudflare.unavailable.pages") })}
      </p>
    {/if}

    {#if !zone}
      <p class="mt-2 text-xs text-text-muted">{t("cloudflare.pages.no_zone_hint")}</p>
    {/if}

    {#if canManage}
      {#if panel.projects.length === 0}
        <p class="mt-2 text-xs text-text-muted">{t("cloudflare.pages.no_projects")}</p>
      {:else}
        <form
          method="POST"
          action="?/cfLinkPages"
          use:enhance={busy.clear("pages")}
          class="mt-3 flex flex-wrap items-end gap-2"
        >
          <div class="min-w-0 flex-1">
            <label class={labelClass} for="cf-project">{t("cloudflare.pages.project")}</label>
            <select id="cf-project" name="project_id" class={inputClass}>
              {#each panel.projects as project (project.id)}
                <!-- The account is named only where the tenant has more than one: two accounts
                     may each hold a project called "site", and the account is what decides
                     which Cloudflare this hostname is registered at. -->
                <option value={project.id}>
                  {panel.accounts.length > 1 && project.account_name
                    ? `${project.name} · ${project.account_name}`
                    : project.name}
                </option>
              {/each}
            </select>
          </div>
          <div class="min-w-0 flex-1">
            <label class={labelClass} for="cf-hostname">{t("cloudflare.pages.hostname")}</label>
            <input
              id="cf-hostname"
              name="hostname"
              placeholder={status?.domain_name ?? ""}
              class={inputClass}
            />
          </div>
          <Button
            type="submit"
            variant="secondary"
            loading={busy.is("pages")}
            disabled={busy.active}
          >
            {t("cloudflare.pages.link")}
          </Button>
        </form>
        <p class="mt-1 text-xs text-text-muted">{t("cloudflare.pages.hostname_help")}</p>
      {/if}
    {/if}
  </section>
{/if}

<ConfirmDialog
  bind:open={confirmRemoveRedirect}
  title={t("cloudflare.redirect.remove")}
  message={t("cloudflare.redirect.remove_confirm", { target: redirect?.target_url ?? "" })}
  action="?/cfRemoveRedirect"
/>

<!-- Deleting a rule the zone already had. Its own dialog rather than the one above, because the
     sentence is different: that one removes schakl's rule and says so, this one removes a rule
     at Cloudflare that schakl may never have made — which is exactly the thing a confirmation
     has to be unambiguous about. -->
<ConfirmDialog
  bind:open={confirmDeleteRule}
  title={t("cloudflare.redirect.delete")}
  message={t("cloudflare.redirect.delete_rule_confirm", { target: deleteRuleTarget?.label ?? "" })}
  action="?/cfDeleteRule"
  fields={{ rule_id: deleteRuleTarget?.id ?? "" }}
/>

<ConfirmDialog
  bind:open={confirmUnlink}
  title={t("cloudflare.pages.unlink")}
  message={t("cloudflare.pages.unlink_confirm", {
    hostname: unlinkTarget?.hostname ?? "",
    project: unlinkTarget?.project ?? "",
  })}
  action="?/cfUnlinkPages"
  fields={{ link_id: unlinkTarget?.id ?? "" }}
/>
