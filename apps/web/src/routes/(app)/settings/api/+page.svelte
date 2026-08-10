<script lang="ts">
  /**
   * Instellingen → API en MCP.
   *
   * Three steps, because minting the key was never the hard part: deciding what it may do, and
   * knowing where to paste it, were. Step 3 only exists because the secret is shown exactly once
   * — the connection line has to be on screen *while* the secret still is, or the user copies a
   * token and then goes looking for docs they no longer have the credential for.
   */
  import { Bot, ChevronDown, Plug, Terminal } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import Button from "$lib/core/ui/Button.svelte";
  import CopyBlock from "$lib/core/ui/CopyBlock.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";

  let { data, form } = $props();
  const busy = new InFlight();

  type Target = "mcp" | "automation" | "custom";
  const ACCESS_MODES = ["read", "full", "custom"] as const;
  type Access = (typeof ACCESS_MODES)[number];

  /** Whether `/mcp` is mounted *and* licensed — the guide is only honest if both hold. */
  const mcpAvailable = $derived(data.mcpEnabled && data.mcpEntitled);

  const readScopes = $derived(data.scopeOptions.filter((s) => s.read).map((s) => s.value));
  const allScopes = $derived(data.scopeOptions.map((s) => s.value));

  // Opening state, deliberately read once: these are the user's choices from here on, and a
  // reload of `data` must not reach in and undo a half-made selection. The instance flags and
  // the permission catalog do not change within a session.
  // A control that would always refuse is not offered as the default (#253): on an instance
  // with no MCP surface the flow opens on the one target that works.
  // svelte-ignore state_referenced_locally
  let target = $state<Target>(data.mcpEnabled && data.mcpEntitled ? "mcp" : "automation");
  // Read-first (CLAUDE.md §12) is a key-minting decision, so it is where the minting happens.
  let access = $state<Access>("read");
  // svelte-ignore state_referenced_locally
  let selected = $state<string[]>(data.scopeOptions.filter((s) => s.read).map((s) => s.value));
  let scopeQuery = $state("");
  // Bound rather than uncontrolled, which is what lets the form say `reset: false` below.
  let keyName = $state("");
  let expiresAt = $state("");

  function applyAccess(next: Access) {
    access = next;
    if (next === "read") selected = [...readScopes];
    else if (next === "full") selected = [...allScopes];
    // "custom" deliberately keeps whatever the preset just put there — the point is to trim it.
  }

  function toggleScope(value: string) {
    selected = selected.includes(value)
      ? selected.filter((v) => v !== value)
      : [...selected, value];
  }

  // Filtering runs over the *resolved* label, because that is the word the user is looking for;
  // the catalog key is matched too, so someone who knows `time.entry` can type that instead.
  const scopeRows = $derived(
    data.scopeOptions.map((s) => ({
      ...s,
      label: t(s.label_key),
      suffix: s.value.includes(":") ? s.value.split(":")[1] : "",
    })),
  );
  const visibleScopes = $derived(
    scopeQuery.trim() === ""
      ? scopeRows
      : scopeRows.filter((s) =>
          `${s.label} ${s.value}`.toLowerCase().includes(scopeQuery.trim().toLowerCase()),
        ),
  );

  // What the connection lines say once the key exists. Before that — and on every later visit,
  // since the API keeps only a hash — the same lines render with a placeholder, so the shape of
  // the command is learnable without minting anything.
  const PLACEHOLDER = "schakl_…";
  const secret = $derived(form?.createdSecret ?? PLACEHOLDER);
  const mcpUrl = $derived(`${data.origin}/mcp`);
  const docsUrl = $derived(`${data.origin}/api/docs`);
  // `schakl` here is the identifier the server registers itself under (app/core/mcp/server.py)
  // and the alias docs/MCP.md prescribes — code, not the tenant's brand (CLAUDE.md §1).
  const claudeCommand = $derived(
    `claude mcp add --transport http schakl ${mcpUrl} \\\n  --header "Authorization: Bearer ${secret}"`,
  );
  const clientConfig = $derived(
    JSON.stringify(
      {
        mcpServers: {
          schakl: {
            type: "http",
            url: mcpUrl,
            headers: { Authorization: `Bearer ${secret}` },
          },
        },
      },
      null,
      2,
    ),
  );
  const curlCommand = $derived(
    `curl -H "X-API-Key: ${secret}" \\\n  ${data.origin}/api/v1/companies`,
  );

  // After a create the guide must match the key that was just minted, not whatever the radios
  // say now — the action echoes the choice back for exactly that reason.
  const shownTarget = $derived((form?.target as Target | undefined) ?? target);

  const cardClass =
    "flex w-full items-start gap-3 rounded-xl border p-4 text-left transition-colors";
</script>

<svelte:head>
  <title>{pageTitle(t("settings.api.title"))}</title>
</svelte:head>

<div class="mb-6">
  <h1 class="mt-1 text-xl font-semibold text-text">{t("settings.api.title")}</h1>
  <p class="mt-1 text-sm text-text-muted">{t("settings.api.subtitle")}</p>
</div>

<div class="max-w-2xl space-y-6">
  <!-- Step 1 — what is being connected. It picks the instructions, never a permission. -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="text-sm font-semibold text-text">{t("settings.api.step_target")}</h2>
    <p class="mt-1 text-sm text-text-muted">{t("settings.api.step_target_help")}</p>

    <div class="mt-4 space-y-2">
      {#if mcpAvailable}
        <button
          type="button"
          class="{cardClass} {target === 'mcp'
            ? 'border-brand bg-brand/5'
            : 'border-border hover:border-brand'}"
          aria-pressed={target === "mcp"}
          onclick={() => (target = "mcp")}
        >
          <Bot size={18} class="mt-0.5 shrink-0 text-text-muted" />
          <span class="min-w-0">
            <span class="block text-sm font-medium text-text">{t("settings.api.target_mcp")}</span>
            <span class="mt-0.5 block text-xs text-text-muted"
              >{t("settings.api.target_mcp_help")}</span
            >
          </span>
        </button>
      {:else}
        <!-- Shown, not hidden: this is how anyone learns the surface exists at all. It is not a
             control, because pressing it could only lead to a command that fails. -->
        <div class="rounded-xl border border-dashed border-border p-4">
          <div class="flex items-start gap-3">
            <Bot size={18} class="mt-0.5 shrink-0 text-text-muted" />
            <div class="min-w-0">
              <p class="text-sm font-medium text-text-muted">{t("settings.api.target_mcp")}</p>
              <p class="mt-0.5 text-xs text-text-muted">
                {data.mcpEnabled
                  ? t("settings.api.mcp_unlicensed")
                  : t("settings.api.mcp_disabled")}
              </p>
            </div>
          </div>
        </div>
      {/if}

      <button
        type="button"
        class="{cardClass} {target === 'automation'
          ? 'border-brand bg-brand/5'
          : 'border-border hover:border-brand'}"
        aria-pressed={target === "automation"}
        onclick={() => (target = "automation")}
      >
        <Plug size={18} class="mt-0.5 shrink-0 text-text-muted" />
        <span class="min-w-0">
          <span class="block text-sm font-medium text-text"
            >{t("settings.api.target_automation")}</span
          >
          <span class="mt-0.5 block text-xs text-text-muted"
            >{t("settings.api.target_automation_help")}</span
          >
        </span>
      </button>

      <button
        type="button"
        class="{cardClass} {target === 'custom'
          ? 'border-brand bg-brand/5'
          : 'border-border hover:border-brand'}"
        aria-pressed={target === "custom"}
        onclick={() => (target = "custom")}
      >
        <Terminal size={18} class="mt-0.5 shrink-0 text-text-muted" />
        <span class="min-w-0">
          <span class="block text-sm font-medium text-text">{t("settings.api.target_custom")}</span>
          <span class="mt-0.5 block text-xs text-text-muted"
            >{t("settings.api.target_custom_help")}</span
          >
        </span>
      </button>
    </div>
  </section>

  <!-- Step 2 — the key itself. -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="text-sm font-semibold text-text">{t("settings.api.step_key")}</h2>
    <p class="mt-1 text-sm text-text-muted">{t("settings.api.step_key_help")}</p>

    <form
      method="POST"
      action="?/createKey"
      class="mt-4 space-y-4"
      use:enhance={busy.wrap("createKey", () => async ({ result, update }) => {
        // A create empties itself for the next one — but never through the browser's reset.
        // Half of this form is state (the access preset, the ticked scopes) rendered as
        // `checked={…}`, and `form.reset()` rewinds a control to its *attribute* default while
        // leaving that state untouched: after a save the radio silently showed nothing selected
        // over a selection that was still there. The same failure docs/UX.md records for text
        // inputs, one control type over. So `reset: false`, and clearing is done here.
        if (result.type === "success") {
          keyName = "";
          expiresAt = "";
          access = "read";
          selected = [...readScopes];
          scopeQuery = "";
        }
        await update({ reset: false });
      })}
    >
      <input type="hidden" name="target" value={target} />
      {#each selected as scope (scope)}
        <input type="hidden" name="scopes" value={scope} />
      {/each}

      <div>
        <label for="key-name" class="mb-1 block text-sm font-medium text-text">
          {t("settings.api.name")}
        </label>
        <input
          id="key-name"
          name="name"
          bind:value={keyName}
          required
          placeholder={t(`settings.api.name_placeholder.${target}`)}
          class="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand"
        />
      </div>

      <div>
        <span class="mb-1 block text-sm font-medium text-text">{t("settings.api.access")}</span>
        <div class="space-y-2">
          {#each ACCESS_MODES as key (key)}
            <label
              class="flex cursor-pointer items-start gap-2 rounded-lg border p-3 {access === key
                ? 'border-brand bg-brand/5'
                : 'border-border hover:border-brand'}"
            >
              <input
                type="radio"
                name="access"
                value={key}
                checked={access === key}
                onchange={() => applyAccess(key)}
                class="mt-0.5 h-3.5 w-3.5"
              />
              <span class="min-w-0">
                <span class="block text-sm text-text">{t(`settings.api.access_${key}`)}</span>
                <span class="mt-0.5 block text-xs text-text-muted"
                  >{t(`settings.api.access_${key}_help`)}</span
                >
              </span>
            </label>
          {/each}
        </div>

        {#if access === "custom"}
          <div class="mt-3">
            <input
              type="search"
              bind:value={scopeQuery}
              placeholder={t("settings.api.scope_search")}
              aria-label={t("settings.api.scope_search")}
              class="mb-2 w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand"
            />
            <div class="max-h-56 space-y-1 overflow-y-auto rounded-lg border border-border p-2">
              {#each visibleScopes as scope (scope.value)}
                <label class="flex items-center gap-2 text-xs text-text">
                  <input
                    type="checkbox"
                    checked={selected.includes(scope.value)}
                    onchange={() => toggleScope(scope.value)}
                    class="h-3.5 w-3.5 rounded border-border"
                  />
                  <span>{scope.label}</span>
                  {#if scope.suffix}
                    <span class="text-text-muted/70">({scope.suffix})</span>
                  {/if}
                </label>
              {:else}
                <p class="p-1 text-xs text-text-muted">{t("common.no_results")}</p>
              {/each}
            </div>
          </div>
        {/if}

        <p class="mt-2 text-xs text-text-muted">
          {t("settings.api.access_count", {
            count: selected.length,
            total: data.scopeOptions.length,
          })}
        </p>
      </div>

      <div>
        <label for="key-expiry" class="mb-1 block text-sm font-medium text-text">
          {t("settings.api.expiry")}
        </label>
        <DateInput name="expires_at" id="key-expiry" bind:value={expiresAt} />
        <p class="mt-1 text-xs text-text-muted">{t("settings.api.expiry_help")}</p>
      </div>

      {#if form?.error}
        <p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
      {/if}
      <Button loading={busy.is("createKey")} disabled={selected.length === 0}>
        {t("settings.api.create")}
      </Button>
    </form>
  </section>

  <!-- Step 3 — connect. Rendered always, with a placeholder secret until there is a real one:
       the command is worth reading before you decide to mint anything. -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="text-sm font-semibold text-text">{t("settings.api.step_connect")}</h2>

    {#if form?.createdSecret}
      <div
        class="mt-3 rounded-lg border border-amber-300 bg-amber-50 p-3 dark:border-amber-800 dark:bg-amber-950"
      >
        <p class="text-xs font-medium text-amber-800 dark:text-amber-200">
          {t("settings.api.created", { name: form.createdName ?? "" })}
        </p>
        <p class="mt-1 text-xs text-amber-800 dark:text-amber-200">{t("settings.api.once")}</p>
      </div>
      <div class="mt-3">
        <CopyBlock value={form.createdSecret} label={t("settings.api.secret_label")} />
      </div>
    {:else}
      <p class="mt-1 text-sm text-text-muted">{t("settings.api.step_connect_help")}</p>
    {/if}

    <div class="mt-4 space-y-4">
      {#if shownTarget === "mcp" && mcpAvailable}
        <CopyBlock
          value={claudeCommand}
          label={t("settings.api.claude_code")}
          help={t("settings.api.claude_code_help")}
        />
        <CopyBlock
          value={clientConfig}
          label={t("settings.api.client_config")}
          help={t("settings.api.client_config_help")}
        />
        <CopyBlock value={mcpUrl} label={t("settings.api.endpoint")} />
      {:else}
        <CopyBlock
          value={curlCommand}
          label={t("settings.api.curl")}
          help={t("settings.api.curl_help")}
        />
      {/if}
      <p class="text-xs text-text-muted">
        {t("settings.api.docs_hint")}
        <a href={docsUrl} target="_blank" rel="noreferrer" class="text-brand hover:underline">
          {docsUrl}
        </a>
      </p>
    </div>
  </section>

  <!-- The keys that already exist. No secret here, ever — the API keeps only a hash. -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="text-sm font-semibold text-text">{t("settings.api.keys_title")}</h2>
    <p class="mt-1 text-sm text-text-muted">{t("settings.api.keys_help")}</p>

    {#if data.apiKeys.length === 0}
      <p class="mt-4 text-sm text-text-muted">{t("settings.api.keys_empty")}</p>
    {:else}
      <ul class="mt-4 divide-y divide-border rounded-lg border border-border">
        {#each data.apiKeys as key (key.id)}
          <li class="flex items-center gap-3 px-3 py-2 text-sm">
            <div class="min-w-0 flex-1">
              <span class="font-medium text-text">{key.name}</span>
              {#if key.revoked_at}
                <span
                  class="ml-2 rounded-full bg-red-100 px-2 py-0.5 text-[11px] text-red-700 dark:bg-red-950 dark:text-red-300"
                  >{t("settings.api.key_revoked")}</span
                >
              {/if}
              <span class="block truncate font-mono text-xs text-text-muted">{key.redacted}</span>
              <span class="block text-xs text-text-muted">
                {t("settings.api.key_scopes", { count: key.scopes.length })} ·
                {key.expires_at
                  ? t("settings.api.key_expires", {
                      // Stored as end-of-day UTC (the create action), so the UTC date part is
                      // the day the user picked. `fmtNumericDate` takes a date-only string.
                      date: fmtNumericDate(key.expires_at.slice(0, 10)),
                    })
                  : t("settings.api.key_no_expiry")}
              </span>
            </div>
            {#if !key.revoked_at}
              <form method="POST" action="?/revokeKey" use:enhance={busy.wrap(`revoke:${key.id}`)}>
                <input type="hidden" name="key_id" value={key.id} />
                <Button variant="danger-outline" size="xs" loading={busy.is(`revoke:${key.id}`)}>
                  {t("settings.api.key_revoke")}
                </Button>
              </form>
            {/if}
          </li>
        {/each}
      </ul>
    {/if}

    <details class="group mt-4">
      <summary
        class="flex cursor-pointer list-none items-center gap-1 text-xs font-medium text-text-muted hover:text-text"
      >
        <ChevronDown size={13} class="transition-transform group-open:rotate-180" />
        {t("settings.api.reuse_title")}
      </summary>
      <p class="mt-2 text-xs text-text-muted">{t("settings.api.reuse_help")}</p>
    </details>
  </section>
</div>
