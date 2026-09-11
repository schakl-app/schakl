<script lang="ts">
  /**
   * "Claude wants to connect to <brand>." One question, the scopes it will get, two buttons.
   *
   * The client's own name and URL are rendered as **text**, never as markup and never as a link:
   * they arrived over an unauthenticated registration endpoint, so they are a stranger's words on
   * a page whose whole purpose is for the reader to trust what it says.
   */
  import { Bot, Check, ShieldCheck } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import Button from "$lib/core/ui/Button.svelte";

  let { data, form } = $props();
  const busy = new InFlight();
  // The tenant's own brand, never the product's (CLAUDE.md §7) — this page is the moment
  // somebody decides whether to trust the connection, so it has to look like their workspace.
  const brand = $derived(page.data.theme?.brandName || "");

  /**
   * Three answers, and the first two are *rules* rather than lists (`apikeys/scopes.py`).
   *
   * "Everything I can do" stores `mcp:full` and "read only" stores `mcp:read`; both expand
   * against the catalog on every request the connector makes, which is what lets it reach a
   * module the agency switches on next quarter. "Choose myself" stores the ticked keys as a
   * fixed list — the honest shape for a person who narrowed the offer, and said so on screen,
   * because a connector consented in August could not name Search Console in September and
   * nothing told anyone why.
   *
   * Which modes are offered is the API's answer (`coarse`): a client that asked for reads is
   * never offered the writes, and a client that named explicit keys gets the list and nothing
   * coarse at all.
   */
  type Mode = "full" | "read" | "custom";
  // Read once, like `selected` below: the offer is decided when the page opens.
  // svelte-ignore state_referenced_locally
  const coarse = data.consent.coarse;
  const MODES = (
    [
      ["full", "mcp:full"],
      ["read", "mcp:read"],
    ] as const
  ).filter(([, scope]) => coarse.includes(scope));
  let mode = $state<Mode>(MODES.length > 0 ? MODES[0][0] : "custom");

  // Every offered scope starts ticked — the API already narrowed the list to what this person
  // holds, so the default is "what the client asked for and you can actually give". Untick is
  // how a person narrows it; there is no control that could widen it, here or at the API.
  //
  // Read once, deliberately: from here on these are the user's ticks, and a re-run of `load`
  // must not reach in and re-tick a box they just cleared on a consent screen.
  // svelte-ignore state_referenced_locally
  let selected = $state<string[]>(data.consent.scopes.map((s) => s.value));

  // What the form posts. A coarse mode posts one token; the custom pick posts its list.
  const posted = $derived(
    mode === "full" ? ["mcp:full"] : mode === "read" ? ["mcp:read"] : selected,
  );

  // Grouped per catalog module, in the catalog's own order, each heading the same
  // `permissions.group.<group>` the roles matrix prints — so the list is a table of contents
  // and not two hundred rows in a scroll box, which is how "not all permissions are here"
  // came to be reported about a box that held every one of them.
  const groups = $derived.by(() => {
    const out: { group: string; scopes: typeof data.consent.scopes }[] = [];
    for (const scope of data.consent.scopes) {
      const entry = out.find((g) => g.group === scope.group);
      if (entry) entry.scopes.push(scope);
      else out.push({ group: scope.group, scopes: [scope] });
    }
    return out;
  });

  function toggle(value: string) {
    selected = selected.includes(value)
      ? selected.filter((v) => v !== value)
      : [...selected, value];
  }

  function toggleGroup(group: { value: string }[], on: boolean) {
    const keys = group.map((s) => s.value);
    selected = on
      ? [...new Set([...selected, ...keys])]
      : selected.filter((v) => !keys.includes(v));
  }

  function suffixLabel(value: string): string | null {
    const suffix = value.split(":")[1];
    if (suffix === "own") return t("oauth.consent.scope_own");
    if (suffix === "any") return t("oauth.consent.scope_any");
    return null;
  }
</script>

<svelte:head>
  <title>{pageTitle(t("oauth.consent.title"))}</title>
  <!-- A consent URL carries a client's `state` and PKCE challenge; nothing here belongs in an
       index or in a referer sent to the client's callback. -->
  <meta name="robots" content="noindex, nofollow" />
  <meta name="referrer" content="no-referrer" />
</svelte:head>

<div class="flex min-h-screen items-center justify-center bg-surface p-4">
  <div class="w-full max-w-lg rounded-2xl border border-border bg-surface-raised p-6 shadow-sm">
    <div class="flex items-center gap-3">
      {#if page.data.theme?.logoUrl}
        <img src={page.data.theme.logoUrl} alt={brand} class="h-8" />
      {:else if brand}
        <span class="text-sm font-semibold text-text">{brand}</span>
      {/if}
      <span class="text-text-muted">↔</span>
      <span class="flex h-8 w-8 items-center justify-center rounded-lg border border-border">
        <Bot size={18} class="text-text-muted" />
      </span>
    </div>

    <h1 class="mt-4 text-lg font-semibold text-text">
      {t("oauth.consent.heading", { client: data.consent.client_name })}
    </h1>
    <p class="mt-1 text-sm text-text-muted">
      {t("oauth.consent.intro", { brand: brand || t("oauth.consent.this_workspace") })}
    </p>
    {#if data.consent.client_uri}
      <!-- Shown, never linked: this string came from an unauthenticated registration call. -->
      <p class="mt-1 font-mono text-xs break-all text-text-muted">{data.consent.client_uri}</p>
    {/if}

    <form
      method="POST"
      action="?/approve"
      class="mt-5"
      use:enhance={busy.wrap("approve", () => async ({ update }) => {
        // Approving navigates away to the client's callback; nothing on this page is reused, so
        // there is no field to keep. `reset: false` anyway, so a failed approval comes back with
        // the scopes the user had ticked rather than silently re-widened to the default.
        await update({ reset: false });
      })}
    >
      <input type="hidden" name="client_id" value={data.request.client_id} />
      <input type="hidden" name="redirect_uri" value={data.request.redirect_uri} />
      <input type="hidden" name="code_challenge" value={data.request.code_challenge} />
      <input
        type="hidden"
        name="code_challenge_method"
        value={data.request.code_challenge_method}
      />
      <input type="hidden" name="state" value={data.request.state} />
      <input type="hidden" name="resource" value={data.request.resource} />
      {#each posted as scope (scope)}
        <input type="hidden" name="scopes" value={scope} />
      {/each}

      <div class="space-y-2">
        {#each [...MODES.map(([key]) => key), "custom"] as key (key)}
          <label
            class="flex cursor-pointer items-start gap-2 rounded-lg border p-3 {mode === key
              ? 'border-brand bg-brand/5'
              : 'border-border hover:border-brand'}"
          >
            <input
              type="radio"
              name="mode"
              value={key}
              checked={mode === key}
              onchange={() => (mode = key as Mode)}
              class="mt-0.5 h-3.5 w-3.5"
            />
            <span class="min-w-0">
              <span class="block text-sm text-text">{t(`oauth.consent.mode_${key}`)}</span>
              <span class="mt-0.5 block text-xs text-text-muted"
                >{t(`oauth.consent.mode_${key}_help`)}</span
              >
            </span>
          </label>
        {/each}
      </div>

      {#if mode === "custom"}
        <p class="mt-3 text-xs text-text-muted">
          {t("oauth.consent.selected", {
            count: selected.length,
            total: data.consent.scopes.length,
          })}
        </p>
        <div
          class="mt-2 max-h-[60vh] space-y-3 overflow-y-auto rounded-lg border border-border p-3"
        >
          {#each groups as { group, scopes } (group)}
            <div>
              <div class="mb-1 flex items-center justify-between">
                <span class="text-xs font-semibold tracking-wide text-text uppercase">
                  {t(`permissions.group.${group}`)}
                </span>
                <button
                  type="button"
                  class="text-xs text-brand hover:underline"
                  onclick={() =>
                    toggleGroup(scopes, !scopes.every((s) => selected.includes(s.value)))}
                >
                  {scopes.every((s) => selected.includes(s.value))
                    ? t("oauth.consent.none")
                    : t("oauth.consent.all")}
                </button>
              </div>
              <div class="space-y-1">
                {#each scopes as scope (scope.value)}
                  <label class="flex items-center gap-2 text-xs text-text">
                    <input
                      type="checkbox"
                      checked={selected.includes(scope.value)}
                      onchange={() => toggle(scope.value)}
                      class="h-3.5 w-3.5 rounded border-border"
                    />
                    <span>{t(scope.label_key)}</span>
                    {#if suffixLabel(scope.value)}
                      <span class="text-text-muted/70">({suffixLabel(scope.value)})</span>
                    {/if}
                    {#if !scope.read}
                      <span
                        class="rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] text-amber-800 dark:bg-amber-950 dark:text-amber-300"
                        >{t("oauth.consent.group_write")}</span
                      >
                    {/if}
                  </label>
                {/each}
              </div>
            </div>
          {/each}
        </div>
      {/if}

      <p class="mt-3 flex items-start gap-2 text-xs text-text-muted">
        <ShieldCheck size={14} class="mt-0.5 shrink-0" />
        <span>{t("oauth.consent.cap")}</span>
      </p>

      {#if form?.error}
        <p class="mt-3 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
      {/if}

      <div class="mt-5 flex items-center gap-2">
        <Button loading={busy.is("approve")} disabled={posted.length === 0}>
          <Check size={16} />
          {t("oauth.consent.approve")}
        </Button>
        <button
          type="submit"
          formaction="?/deny"
          class="rounded-lg px-3 py-2 text-sm text-text-muted hover:text-text"
        >
          {t("oauth.consent.deny")}
        </button>
      </div>
    </form>
  </div>
</div>
