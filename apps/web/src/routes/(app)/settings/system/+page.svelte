<script lang="ts">
  import { dateLocale } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import { moduleLabel } from "$lib/core/registry";

  let { data } = $props();

  const info = $derived(data.info);
  const update = $derived(info.update);
  /** An unstamped checkout. The API keeps the update check quiet for these; say why. */
  const isDevBuild = $derived(info.build.version.endsWith("+dev"));

  /** "9 jul 2026, 14:32" — timestamps here span years, unlike the activity feed's. */
  function fmtStamp(iso: string | null | undefined): string {
    if (!iso) return t("settings.system.unknown");
    const parsed = new Date(iso);
    if (Number.isNaN(parsed.getTime())) return t("settings.system.unknown");
    return new Intl.DateTimeFormat(dateLocale(), {
      day: "numeric",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(parsed);
  }

  const dependencies = $derived([
    { key: "database", label: t("settings.system.database"), ...info.database, extra: null },
    { key: "redis", label: t("settings.system.redis"), ...info.redis, extra: null },
    {
      key: "worker",
      label: t("settings.system.worker"),
      status: info.worker.status,
      version: null,
      extra:
        info.worker.status === "ok"
          ? t("settings.system.last_seen", { time: fmtStamp(info.worker.last_seen_at) })
          : null,
    },
  ]);

  /** A single paste-able blob for bug reports — feeds .github/ISSUE_TEMPLATE/bug_report.yml. */
  const diagnostics = $derived(
    [
      `version:     ${info.build.version}`,
      `git sha:     ${info.build.git_sha}`,
      `built at:    ${info.build.built_at ?? "-"}`,
      `environment: ${info.build.environment}`,
      `python:      ${info.build.python_version}`,
      `database:    ${info.database.status} (${info.database.version ?? "-"})`,
      `redis:       ${info.redis.status} (${info.redis.version ?? "-"})`,
      `worker:      ${info.worker.status}, queue ${info.worker.queue_depth ?? "-"}`,
      `migrations:  ${info.migrations.current.join(",") || "-"} → ${info.migrations.head.join(",") || "-"}` +
        ` (${info.migrations.up_to_date ? "up to date" : "PENDING"})`,
      `modules:     ${info.enabled_modules.join(", ")}`,
      `server time: ${info.server_time}`,
    ].join("\n"),
  );

  let copied = $state(false);

  async function copyDiagnostics() {
    try {
      await navigator.clipboard.writeText(diagnostics);
      copied = true;
      setTimeout(() => (copied = false), 2000);
    } catch {
      // Clipboard blocked (insecure origin, permissions) — the <details> block below is the
      // fallback: it holds the same text, selectable.
      copied = false;
    }
  }
</script>

<svelte:head>
  <title>{pageTitle(t("settings.system.title"))}</title>
</svelte:head>

<div class="mb-6">
  <h1 class="mt-2 text-xl font-semibold text-text">{t("settings.system.title")}</h1>
  <p class="mt-1 text-sm text-text-muted">{t("settings.system.subtitle")}</p>
</div>

<div class="max-w-2xl space-y-6">
  <!-- Version + update -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="text-sm font-semibold text-text">{t("settings.system.build")}</h2>

    <p class="mt-3 break-words font-mono text-2xl font-semibold text-text">{info.build.version}</p>
    {#if isDevBuild}
      <p class="mt-1 text-xs text-text-muted">{t("settings.system.dev_build")}</p>
    {/if}

    <dl class="mt-4 grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
      <div class="flex min-w-0 justify-between gap-4 sm:block">
        <dt class="text-text-muted">{t("settings.system.git_sha")}</dt>
        <dd class="min-w-0 truncate font-mono text-text">{info.build.git_sha}</dd>
      </div>
      <div class="flex min-w-0 justify-between gap-4 sm:block">
        <dt class="text-text-muted">{t("settings.system.built_at")}</dt>
        <dd class="text-text">{fmtStamp(info.build.built_at)}</dd>
      </div>
      <div class="flex min-w-0 justify-between gap-4 sm:block">
        <dt class="text-text-muted">{t("settings.system.environment")}</dt>
        <dd class="text-text">{info.build.environment}</dd>
      </div>
      <div class="flex min-w-0 justify-between gap-4 sm:block">
        <dt class="text-text-muted">{t("settings.system.python")}</dt>
        <dd class="min-w-0 truncate font-mono text-text">{info.build.python_version}</dd>
      </div>
    </dl>

    <!-- Update state. Never auto-updates; this is a notice, not an action. -->
    <div class="mt-5 border-t border-border pt-4">
      {#if !update.enabled}
        <p class="text-sm text-text-muted">{t("settings.system.update_disabled")}</p>
        <p class="mt-1 text-xs text-text-muted">{t("settings.system.update_disabled_help")}</p>
      {:else if isDevBuild}
        <p class="text-sm text-text-muted">{t("settings.system.update_dev_build")}</p>
      {:else if update.update_available}
        <div
          class="rounded-lg border border-amber-300 bg-amber-100 dark:border-amber-800 dark:bg-amber-950 p-3"
        >
          <p class="text-sm font-medium text-amber-800 dark:text-amber-300">
            {t("settings.system.update_available", { version: update.latest ?? "" })}
          </p>
          {#if update.release_url}
            <a
              href={update.release_url}
              target="_blank"
              rel="noreferrer noopener"
              class="mt-1 inline-block text-sm font-medium text-amber-800 dark:text-amber-300 underline hover:opacity-80"
            >
              {t("settings.system.update_notes")}
            </a>
          {/if}
        </div>
      {:else if update.latest}
        <p class="text-sm text-green-600 dark:text-green-400">{t("settings.system.up_to_date")}</p>
      {:else}
        <p class="text-sm text-text-muted">{t("settings.system.update_unknown")}</p>
      {/if}

      {#if update.enabled && update.checked_at}
        <p class="mt-2 text-xs text-text-muted">
          {t("settings.system.checked_at", { time: fmtStamp(update.checked_at) })}
        </p>
      {/if}
    </div>
  </section>

  <!-- Dependencies -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="text-sm font-semibold text-text">{t("settings.system.status")}</h2>
    <ul class="mt-3 divide-y divide-border">
      {#each dependencies as dep (dep.key)}
        {@const isOk = dep.status === "ok"}
        <li class="flex items-center gap-3 py-2.5">
          <span
            class="h-2 w-2 shrink-0 rounded-full {isOk ? 'bg-green-500' : 'bg-red-500'}"
            aria-hidden="true"
          ></span>
          <span class="flex-1 text-sm font-medium text-text">{dep.label}</span>
          {#if dep.version}
            <span class="font-mono text-xs text-text-muted">{dep.version}</span>
          {/if}
          {#if dep.extra}
            <span class="hidden text-xs text-text-muted sm:inline">{dep.extra}</span>
          {/if}
          <span
            class="text-sm {isOk
              ? 'text-green-600 dark:text-green-400'
              : 'font-medium text-red-600 dark:text-red-400'}"
          >
            {isOk ? t("settings.system.ok") : t("settings.system.down")}
          </span>
        </li>
      {/each}
    </ul>
    <p class="mt-3 text-xs text-text-muted">
      {t("settings.system.queue_depth", {
        count: info.worker.queue_depth ?? t("settings.system.unknown"),
      })}
    </p>
  </section>

  <!-- Migrations: the classic unattended-upgrade failure mode. -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="text-sm font-semibold text-text">{t("settings.system.migrations")}</h2>
    {#if info.migrations.up_to_date}
      <p class="mt-2 text-sm text-green-600 dark:text-green-400">
        {t("settings.system.migrations_up_to_date")}
      </p>
    {:else}
      <p class="mt-2 text-sm font-medium text-red-600 dark:text-red-400">
        {t("settings.system.migrations_pending")}
      </p>
    {/if}
    <dl class="mt-3 grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
      <div class="flex min-w-0 justify-between gap-4 sm:block">
        <dt class="text-text-muted">{t("settings.system.migration_current")}</dt>
        <dd class="min-w-0 break-all font-mono text-text">{info.migrations.current.join(", ") || "—"}</dd>
      </div>
      <div class="flex min-w-0 justify-between gap-4 sm:block">
        <dt class="text-text-muted">{t("settings.system.migration_head")}</dt>
        <dd class="min-w-0 break-all font-mono text-text">{info.migrations.head.join(", ") || "—"}</dd>
      </div>
    </dl>
  </section>

  <!-- Enabled modules -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="text-sm font-semibold text-text">{t("settings.system.modules")}</h2>
    <ul class="mt-3 flex flex-wrap gap-2">
      {#each info.enabled_modules as moduleName (moduleName)}
        <li class="rounded-full bg-surface px-2.5 py-0.5 text-xs font-medium text-text-muted">
          {moduleLabel(moduleName)}
        </li>
      {/each}
    </ul>
  </section>

  <!-- Diagnostics blob -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="text-sm font-semibold text-text">{t("settings.system.diagnostics")}</h2>
    <p class="mt-1 text-sm text-text-muted">{t("settings.system.diagnostics_help")}</p>
    <button
      onclick={copyDiagnostics}
      class="mt-3 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
    >
      {copied ? t("settings.system.copied") : t("settings.system.copy")}
    </button>
    <details class="mt-3">
      <summary class="cursor-pointer text-sm text-text-muted hover:text-text">
        {t("settings.system.diagnostics_show")}
      </summary>
      <pre
        class="mt-2 overflow-x-auto rounded-lg bg-surface p-3 font-mono text-xs text-text">{diagnostics}</pre>
    </details>
  </section>
</div>
