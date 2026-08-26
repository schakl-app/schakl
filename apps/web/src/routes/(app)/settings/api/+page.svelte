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
  import { t, tn } from "$lib/core/i18n";
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

  /**
   * Which slice of the tool surface the printed connection points at.
   *
   * The whole surface is ~620 tools and about two megabytes of `tools/list`; a chat client loads
   * every tool into the model's context on every turn, and ChatGPT refuses anything over ~5,000
   * tokens for all of them together. So this is not a preference — for most clients it is the
   * difference between a connector that adds and one that does not, which is why the tool count
   * rides every option instead of a paragraph underneath (docs/MCP.md).
   *
   * `""` is `/mcp` itself: the absence of a section, not a section named "everything".
   */
  let section = $state("");
  const sections = $derived(data.mcpSections ?? []);
  const chosen = $derived(sections.find((s) => s.key === section));
  const KINDS = ["curated", "bundle", "module"] as const;

  function sectionLabel(row: { key: string; label_key: string }): string {
    // A module section labels itself with the key the modules screen already uses, and that key
    // falls back to the raw name for a module this build does not know — so an instance running
    // a module newer than its web bundle names it, rather than printing an i18n key at somebody.
    const label = t(row.label_key);
    return label === row.label_key ? row.key : label;
  }

  // What the connection lines say once the key exists. Before that — and on every later visit,
  // since the API keeps only a hash — the same lines render with a placeholder, so the shape of
  // the command is learnable without minting anything.
  const PLACEHOLDER = "schakl_…";
  const secret = $derived(form?.createdSecret ?? PLACEHOLDER);
  const mcpUrl = $derived(`${data.origin}/mcp${section ? `/${section}` : ""}`);
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
        <!-- Which tools the connection offers. Above the commands, because it changes them. -->
        <div>
          <span class="mb-1 block text-sm font-medium text-text">
            {t("settings.api.section_label")}
          </span>
          <p class="mb-2 text-xs text-text-muted">
            {t("settings.api.section_help", { total: data.mcpTotalTools })}
          </p>
          <div class="flex flex-wrap gap-1.5">
            <button
              type="button"
              class="rounded-full border px-2.5 py-1 text-xs transition-colors {section === ''
                ? 'border-brand bg-brand/10 text-text'
                : 'border-border text-text-muted hover:border-brand'}"
              aria-pressed={section === ""}
              onclick={() => (section = "")}
            >
              {t("settings.api.section_all")}
              <span class="text-text-muted/70">({data.mcpTotalTools})</span>
            </button>
            {#each KINDS as kind (kind)}
              {#each sections.filter((s) => s.kind === kind) as row (row.key)}
                <button
                  type="button"
                  class="rounded-full border px-2.5 py-1 text-xs transition-colors {section ===
                  row.key
                    ? 'border-brand bg-brand/10 text-text'
                    : 'border-border text-text-muted hover:border-brand'}"
                  aria-pressed={section === row.key}
                  onclick={() => (section = row.key)}
                >
                  {sectionLabel(row)}
                  <span class="text-text-muted/70">({row.tool_count})</span>
                </button>
              {/each}
            {/each}
          </div>
          {#if chosen}
            <p class="mt-2 text-xs text-text-muted">
              {#if chosen.kind === "bundle"}
                {t("settings.api.section_bundle_note", {
                  modules: (chosen.modules ?? [])
                    .map((m) => {
                      const label = t(`module.${m}.label`);
                      return label === `module.${m}.label` ? m : label;
                    })
                    .join(", "),
                })}
              {:else if chosen.kind === "curated"}
                {t(`settings.api.section.${chosen.key}_help`)}
              {:else}
                {t("settings.api.section_module_note", { name: sectionLabel(chosen) })}
              {/if}
            </p>
          {/if}
          <!-- A section narrows what a client is *offered*, never what a credential may do
               (CLAUDE.md §12). Said on the screen because the opposite reading is the natural
               one, and acting on it would leave somebody believing a URL is a security control. -->
          <p class="mt-1 text-xs text-text-muted/80">{t("settings.api.section_not_a_gate")}</p>
        </div>

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
        <div class="rounded-lg border border-dashed border-border p-3">
          <p class="text-xs font-medium text-text">{t("settings.api.oauth_title")}</p>
          <p class="mt-1 text-xs text-text-muted">{t("settings.api.oauth_help")}</p>
        </div>
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

  <!-- Clients connected over OAuth. Their own card rather than a row among the keys, because a
       person recognises "Claude" and not the key it happens to hold — and disconnecting revokes
       the client, so every session it ever opened goes with it. -->
  {#if data.connections.length > 0}
    <section class="rounded-xl border border-border bg-surface-raised p-5">
      <h2 class="text-sm font-semibold text-text">{t("settings.api.connections_title")}</h2>
      <p class="mt-1 text-sm text-text-muted">{t("settings.api.connections_help")}</p>
      <ul class="mt-4 divide-y divide-border rounded-lg border border-border">
        {#each data.connections as connection (connection.id)}
          <li class="flex items-center gap-3 px-3 py-2 text-sm">
            <Bot size={16} class="shrink-0 text-text-muted" />
            <div class="min-w-0 flex-1">
              <!-- The client's own name, from an unauthenticated registration call: text only. -->
              <span class="font-medium text-text">{connection.client_name}</span>
              <span class="block text-xs text-text-muted">
                {connection.sessions > 0
                  ? t("settings.api.connection_active")
                  : t("settings.api.connection_idle")}
              </span>
            </div>
            <form
              method="POST"
              action="?/disconnect"
              use:enhance={busy.wrap(`disconnect:${connection.id}`)}
            >
              <input type="hidden" name="client_pk" value={connection.id} />
              <Button
                variant="danger-outline"
                size="xs"
                loading={busy.is(`disconnect:${connection.id}`)}
              >
                {t("settings.api.connection_disconnect")}
              </Button>
            </form>
          </li>
        {/each}
      </ul>
    </section>
  {/if}

  <!-- Manual OAuth clients (#441) — admin surface. DCR clients (Claude) need none of this:
       they register themselves with only the /mcp URL. This exists for the connectors that
       ask an operator for a client id + secret and will not DCR. -->
  {#if data.oauthClients !== null}
    <section class="rounded-xl border border-border bg-surface-raised p-5">
      <h2 class="text-sm font-semibold text-text">{t("settings.api.clients_title")}</h2>
      <p class="mt-1 text-sm text-text-muted">{t("settings.api.clients_help")}</p>

      {#if form?.clientSecret}
        <div class="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-3 dark:border-amber-700 dark:bg-amber-950">
          <p class="text-sm font-medium text-amber-900 dark:text-amber-200">
            {t(form?.rotated ? "settings.api.client_rotated" : "settings.api.client_created", {
              name: form.clientName ?? "",
            })}
          </p>
          <p class="mt-0.5 text-xs text-amber-800 dark:text-amber-300">{t("settings.api.once")}</p>
          <div class="mt-3 space-y-2">
            <CopyBlock value={form.clientId ?? ""} label={t("settings.api.client_id_label")} />
            <CopyBlock value={form.clientSecret} label={t("settings.api.client_secret_label")} />
          </div>
        </div>
      {/if}

      {#if data.oauthClients.length > 0}
        <ul class="mt-4 divide-y divide-border rounded-lg border border-border">
          {#each data.oauthClients as client (client.id)}
            <li class="flex flex-wrap items-center gap-3 px-3 py-2 text-sm">
              <Bot size={16} class="shrink-0 text-text-muted" />
              <div class="min-w-0 flex-1">
                <span class="font-medium text-text">{client.client_name}</span>
                <span class="block truncate text-xs text-text-muted">
                  {client.client_id}
                  · {t(client.manual ? "settings.api.client_manual" : "settings.api.client_dcr")}
                </span>
              </div>
              {#if client.manual}
                <form
                  method="POST"
                  action="?/rotateClient"
                  use:enhance={busy.wrap(`rotate:${client.id}`)}
                >
                  <input type="hidden" name="client_pk" value={client.id} />
                  <Button variant="secondary" size="xs" loading={busy.is(`rotate:${client.id}`)}>
                    {t("settings.api.client_rotate")}
                  </Button>
                </form>
              {/if}
              <form
                method="POST"
                action="?/revokeClient"
                use:enhance={busy.wrap(`revokeClient:${client.id}`)}
              >
                <input type="hidden" name="client_pk" value={client.id} />
                <Button
                  variant="danger-outline"
                  size="xs"
                  loading={busy.is(`revokeClient:${client.id}`)}
                >
                  {t("settings.api.client_revoke")}
                </Button>
              </form>
            </li>
          {/each}
        </ul>
      {/if}

      <!-- One create form: name + one redirect URI per line. Cleared on success — the reveal
           block above is what carries the result, and a fresh form is what the next client
           wants. -->
      <form
        method="POST"
        action="?/createClient"
        use:enhance={busy.clear("createClient")}
        class="mt-4 space-y-3"
      >
        <div class="grid gap-3 sm:grid-cols-2">
          <div>
            <label for="oauth-client-name" class="mb-1 block text-xs font-medium text-text-muted"
              >{t("settings.api.client_name_label")}</label
            >
            <input
              id="oauth-client-name"
              name="client_name"
              required
              maxlength="200"
              class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text"
              placeholder={t("settings.api.client_name_placeholder")}
            />
          </div>
          <div>
            <label
              for="oauth-client-redirects"
              class="mb-1 block text-xs font-medium text-text-muted"
              >{t("settings.api.client_redirects_label")}</label
            >
            <textarea
              id="oauth-client-redirects"
              name="redirect_uris"
              required
              rows="2"
              class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text"
              placeholder="https://voorbeeld.nl/oauth/callback"
            ></textarea>
            <p class="mt-1 text-xs text-text-muted">{t("settings.api.client_redirects_help")}</p>
          </div>
        </div>
        <Button type="submit" size="sm" loading={busy.is("createClient")}>
          {t("settings.api.client_create")}
        </Button>
      </form>
    </section>
  {/if}

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
                {tn("settings.api.key_scopes", key.scopes.length)} ·
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
