<script lang="ts">
  import { enhance } from "$app/forms";
  import { fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import Button from "$lib/core/ui/Button.svelte";
  import { getLocale } from "$lib/paraglide/runtime";

  let { data, form } = $props();

  const busy = new InFlight();

  // The freshest status wins: a check/claim response over the SSR load. The wizard is
  // resumable — the load's persisted stage is where the customer left off (#292).
  const report = $derived(form?.report ?? null);
  const status = $derived(report?.status ?? form?.status ?? data.domain);
  const stage = $derived(status?.stage ?? "none");
  const checks = $derived(report?.checks ?? []);
  const domainName = $derived(status?.pending_domain ?? status?.custom_domain ?? "");

  // Cloudflare manages this domain's certificate lifecycle (#291): there is state worth
  // showing. A Traefik/Let's Encrypt domain (every self-host box) has none — verified is live.
  const hasLifecycle = $derived(Boolean(status?.hostname_status || status?.checked_at));

  // Step 4 lights up once traffic DNS is observed; before that the customer is on step 3.
  const targetSeen = $derived(
    checks.some((c: { key: string; state: string }) => c.key === "dns_target" && c.state === "ok"),
  );
  const stepIndex = $derived(
    stage === "none"
      ? 1
      : stage === "ownership_pending"
        ? 2
        : stage === "routing_pending"
          ? targetSeen
            ? 4
            : 3
          : 4,
  );
  const steps = [
    "settings.domain.step.choose",
    "settings.domain.step.ownership",
    "settings.domain.step.routing",
    "settings.domain.step.activate",
  ];

  let domainInput = $state("");
  const inputClass =
    "w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text " +
    "focus:outline-none focus:ring-2 focus:ring-brand/40";

  // Copy-paste cards (#292): every DNS field is copyable on its own.
  let copiedField = $state<string | null>(null);
  async function copy(fieldId: string, value: string) {
    try {
      await navigator.clipboard.writeText(value);
      copiedField = fieldId;
      setTimeout(() => (copiedField = null), 1600);
    } catch {
      copiedField = null;
    }
  }

  // Poll while waiting on DNS/certificate — resubmits the check form, never the page.
  let checkForm: HTMLFormElement | undefined = $state();
  $effect(() => {
    if (stage !== "ownership_pending" && stage !== "routing_pending") return;
    const timer = setInterval(() => {
      if (!busy.active) checkForm?.requestSubmit();
    }, 30_000);
    return () => clearInterval(timer);
  });

  const checkedAt = $derived(
    report ? new Date(report.checked_at).toLocaleTimeString(getLocale()) : null,
  );

  function stateBadge(state: string): string {
    if (state === "ok")
      return "bg-green-100 text-green-700 dark:bg-green-950/40 dark:text-green-400";
    if (state === "failed") return "bg-red-100 text-red-700 dark:bg-red-950/40 dark:text-red-400";
    return "bg-amber-100 text-amber-700 dark:bg-amber-950/40 dark:text-amber-400";
  }
</script>

<svelte:head>
  <title>{pageTitle(t("settings.domain.title"))}</title>
</svelte:head>

<h1 class="mb-1 text-lg font-semibold text-text">{t("settings.domain.title")}</h1>
<p class="mb-5 max-w-2xl text-sm text-text-muted">{t("settings.domain.subtitle")}</p>

<!-- Stepper -->
<ol class="mb-6 flex max-w-2xl flex-wrap items-center gap-2 text-xs">
  {#each steps as key, index (key)}
    {@const number = index + 1}
    <li class="flex items-center gap-2">
      <span
        class="flex h-6 w-6 items-center justify-center rounded-full text-[11px] font-semibold
        {number < stepIndex || stage === 'active'
          ? 'bg-green-600 text-white'
          : number === stepIndex
            ? 'bg-brand text-white'
            : 'border border-border text-text-muted'}"
      >
        {number < stepIndex || stage === "active" ? "✓" : number}
      </span>
      <span class={number === stepIndex ? "font-medium text-text" : "text-text-muted"}>
        {t(key)}
      </span>
      {#if number < steps.length}<span class="text-text-muted/50">→</span>{/if}
    </li>
  {/each}
</ol>

{#snippet recordCard(record: {
  purpose: string;
  type: string;
  name: string;
  host: string;
  value: string;
  ttl: number;
  temporary: boolean;
})}
  <div class="rounded-xl border border-border bg-surface-raised p-4">
    <div class="mb-2 flex items-center justify-between gap-2">
      <span class="text-xs font-semibold uppercase tracking-wide text-text-muted">
        {record.type} · {t(
          `settings.domain.status.${record.purpose === "ownership" ? "ownership" : "dns_target"}`,
        )}
      </span>
      <span
        class="rounded-full px-2 py-0.5 text-[11px] font-medium
        {record.temporary
          ? 'bg-amber-100 text-amber-700 dark:bg-amber-950/40 dark:text-amber-400'
          : 'bg-brand/10 text-brand'}"
      >
        {t(
          record.temporary
            ? "settings.domain.record.temporary"
            : "settings.domain.record.permanent",
        )}
      </span>
    </div>
    <dl class="space-y-1.5 text-sm">
      {#each [["type", t("settings.domain.record.type"), record.type], ["name", t("settings.domain.record.name"), record.name], ["value", t("settings.domain.record.value"), record.value], ["ttl", t("settings.domain.record.ttl"), String(record.ttl)]] as [fieldId, label, value] (fieldId)}
        <div class="flex items-start gap-2">
          <dt class="w-14 shrink-0 pt-1 text-xs text-text-muted">{label}</dt>
          <dd
            class="min-w-0 grow break-all rounded bg-surface px-2 py-1 font-mono text-xs text-text"
          >
            {value}
          </dd>
          <button
            type="button"
            class="shrink-0 rounded-lg border border-border px-2 py-1 text-xs text-text-muted hover:text-text"
            onclick={() => copy(`${record.purpose}-${fieldId}`, value)}
          >
            {copiedField === `${record.purpose}-${fieldId}`
              ? t("settings.domain.copied")
              : t("settings.domain.copy")}
          </button>
        </div>
      {/each}
    </dl>
    {#if record.host !== record.name}
      <p class="mt-2 text-xs text-text-muted">
        {t("settings.domain.host_hint", { host: record.host })}
      </p>
    {/if}
  </div>
{/snippet}

{#snippet checkPanel()}
  <!-- The independent per-layer states (#292): never one collapsed "verified" flag. -->
  {#if checks.length}
    <ul class="mt-4 space-y-2">
      {#each checks as check (check.key)}
        <li class="rounded-lg border border-border bg-surface-raised p-3">
          <div class="flex items-center justify-between gap-2">
            <span class="text-sm font-medium text-text">
              {t(`settings.domain.status.${check.key}`)}
            </span>
            <span
              class="rounded-full px-2 py-0.5 text-[11px] font-medium {stateBadge(check.state)}"
            >
              {t(`settings.domain.state.${check.state}`)}
            </span>
          </div>
          <p class="mt-1 text-xs text-text-muted">{t(check.message_key)}</p>
          {#if check.state !== "ok" && (check.expected || check.observed)}
            <dl class="mt-2 space-y-0.5 font-mono text-[11px]">
              {#if check.expected}
                <div class="flex gap-2">
                  <dt class="shrink-0 text-text-muted">{t("settings.domain.expected")}:</dt>
                  <dd class="break-all text-text">{check.expected}</dd>
                </div>
              {/if}
              {#if check.observed}
                <div class="flex gap-2">
                  <dt class="shrink-0 text-text-muted">{t("settings.domain.observed")}:</dt>
                  <dd class="break-all text-text">{check.observed}</dd>
                </div>
              {/if}
            </dl>
          {/if}
        </li>
      {/each}
    </ul>
    <div class="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-text-muted">
      {#if checkedAt}<span>{t("settings.domain.last_checked", { time: checkedAt })}</span>{/if}
      {#if report && checks.some((c: { state: string }) => c.state !== "ok")}
        <span>{t("settings.domain.correlation", { id: report.correlation_id })}</span>
      {/if}
    </div>
  {/if}
  {#if report?.provider_name}
    <p class="mt-2 text-xs text-text-muted">
      {t("settings.domain.provider_hint", { provider: report.provider_name })}
    </p>
  {/if}
{/snippet}

{#snippet checkButton()}
  <form
    method="POST"
    action="?/check"
    bind:this={checkForm}
    use:enhance={busy.wrap("check")}
    class="mt-4 inline-block"
  >
    <Button size="sm" loading={busy.is("check")} disabled={busy.active}>
      {t("settings.domain.check")}
    </Button>
  </form>
{/snippet}

<div class="max-w-2xl space-y-4">
  {#if stage === "none"}
    <!-- Step 1: choose -->
    <section class="rounded-xl border border-border bg-surface-raised p-5">
      <p class="text-sm text-text-muted">{t("settings.domain.choose.explain")}</p>
      <form
        method="POST"
        action="?/claim"
        use:enhance={busy.keep("claim")}
        class="mt-4 flex flex-wrap items-end gap-3"
      >
        <div class="grow">
          <label for="domain" class="mb-1 block text-sm font-medium text-text">
            {t("settings.domain.choose.label")}
          </label>
          <input
            id="domain"
            name="domain"
            bind:value={domainInput}
            placeholder={t("settings.domain.choose.placeholder")}
            class="{inputClass} font-mono"
          />
        </div>
        <Button loading={busy.is("claim")} disabled={busy.active}>
          {t("settings.domain.choose.submit")}
        </Button>
      </form>
      {#if domainInput.trim()}
        <p class="mt-2 text-xs text-text-muted">
          {t("settings.domain.choose.preview", {
            url: `https://${domainInput.trim().toLowerCase()}`,
          })}
        </p>
      {/if}
      {#if form?.claimError}
        <p class="mt-2 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
      {/if}
    </section>
  {:else if stage === "ownership_pending"}
    <!-- Step 2: prove ownership — only the TXT challenge, never the CNAME yet (#292). -->
    <section class="rounded-xl border border-border bg-surface-raised p-5">
      <h2 class="text-sm font-semibold text-text">
        {t("settings.domain.ownership.title", { domain: domainName })}
      </h2>
      <p class="mt-1 text-sm text-text-muted">{t("settings.domain.ownership.explain")}</p>
      <div class="mt-4 space-y-3">
        {#each status?.records ?? [] as record (record.purpose)}
          {@render recordCard(record)}
        {/each}
      </div>
      {@render checkButton()}
      {@render checkPanel()}
      <p class="mt-3 text-xs text-text-muted">{t("settings.domain.propagation")}</p>
    </section>
  {:else if stage === "routing_pending"}
    <!-- Steps 3–4: point DNS, then watch hostname + certificate come up. -->
    <section class="rounded-xl border border-border bg-surface-raised p-5">
      <h2 class="text-sm font-semibold text-text">
        {t("settings.domain.routing.title", { domain: domainName })}
      </h2>
      <p class="mt-1 text-sm text-text-muted">{t("settings.domain.routing.explain")}</p>
      {#if status?.apex}
        <p
          class="mt-2 rounded-lg bg-amber-50 p-2 text-xs text-amber-800 dark:bg-amber-950/30 dark:text-amber-300"
        >
          {t("settings.domain.routing.apex_note")}
        </p>
      {/if}
      <div class="mt-4 space-y-3">
        {#each status?.records ?? [] as record (record.purpose)}
          {@render recordCard(record)}
        {/each}
      </div>
      {@render checkButton()}
      {@render checkPanel()}
      <p class="mt-3 text-xs text-text-muted">{t("settings.domain.propagation")}</p>
    </section>
  {:else}
    <!-- Active: monitor + manage. -->
    <section class="rounded-xl border border-border bg-surface-raised p-5">
      <h2 class="text-sm font-semibold text-green-700 dark:text-green-400">
        {t("settings.domain.active.title", { domain: domainName })}
      </h2>
      <p class="mt-1 text-sm text-text-muted">{t("settings.domain.active.explain")}</p>
      <a
        href={`https://${domainName}`}
        target="_blank"
        rel="noopener noreferrer"
        class="mt-2 inline-block text-sm font-medium text-brand hover:underline"
      >
        {t("settings.domain.active.open", { url: `https://${domainName}` })}
      </a>

      <!-- Certificate lifecycle (#291). Activation is not the end: the hostname must stay
           active, the certificate must keep renewing, and the customer's DNS must keep
           pointing here — otherwise the domain stops being canonical and the org falls back
           to its recovery address rather than to a TLS error. -->
      {#if status?.live}
        <p class="mt-2 text-xs text-green-600 dark:text-green-400">
          {t("settings.domain.health.live")}
        </p>
      {:else}
        <p class="mt-2 text-xs text-amber-600 dark:text-amber-400">
          {t("settings.domain.health.not_live", { host: status?.recovery_host ?? "" })}
        </p>
      {/if}

      {#if hasLifecycle}
        <!-- Status words are the edge network's own vocabulary ("active",
             "pending_validation", …): external system state, shown as data, never translated. -->
        <dl class="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
          <dt class="text-text-muted">{t("settings.domain.health.hostname")}</dt>
          <dd class="font-mono text-text">{status?.hostname_status ?? "—"}</dd>
          <dt class="text-text-muted">{t("settings.domain.health.certificate")}</dt>
          <dd class="font-mono text-text">
            {status?.ssl_status ?? "—"}{#if status?.cert_expires_at}
              <span class="ml-2 font-sans text-text-muted">
                {t("settings.domain.health.expires", {
                  date: fmtNumericDate(status.cert_expires_at),
                })}
              </span>
            {/if}
          </dd>
          <dt class="text-text-muted">{t("settings.domain.health.dns")}</dt>
          <dd class="text-text">
            {status?.dns_ok === true
              ? t("settings.domain.health.dns_ok")
              : status?.dns_ok === false
                ? t("settings.domain.health.dns_moved")
                : t("settings.domain.health.dns_unknown")}
          </dd>
          {#if status?.checked_at}
            <dt class="text-text-muted">{t("settings.domain.health.checked_at")}</dt>
            <dd class="text-text">{fmtNumericDate(status.checked_at)}</dd>
          {/if}
        </dl>
        {#if status?.check_error}
          <p class="mt-2 font-mono text-xs text-red-600 dark:text-red-400">
            {status.check_error}
          </p>
        {/if}
      {/if}

      {#if status?.live && status?.recovery_host}
        <p class="mt-3 text-xs text-text-muted">
          {t("settings.domain.health.canonical_note", {
            host: status.recovery_host,
            domain: status.custom_domain ?? domainName,
          })}
        </p>
      {/if}

      <div class="mt-4 space-y-3">
        {#each status?.records ?? [] as record (record.purpose)}
          {@render recordCard(record)}
        {/each}
      </div>
      {@render checkButton()}
      {@render checkPanel()}
    </section>
  {/if}

  {#if stage !== "none"}
    <section
      class="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-surface-raised p-4"
    >
      <p class="text-xs text-text-muted">
        {t("settings.domain.remove_warning", { fallback: data.fallbackHost })}
      </p>
      <form method="POST" action="?/clear" use:enhance={busy.wrap("clear")}>
        <Button variant="secondary" size="sm" loading={busy.is("clear")} disabled={busy.active}>
          {stage === "active" ? t("settings.domain.remove") : t("settings.domain.cancel_claim")}
        </Button>
      </form>
    </section>
  {/if}

  {#if form?.error && !form?.claimError}
    <p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
  {/if}
</div>
