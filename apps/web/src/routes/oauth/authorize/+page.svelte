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

  // Every offered scope starts ticked — the API already narrowed the list to what this person
  // holds, so the default is "what the client asked for and you can actually give". Untick is
  // how a person narrows it; there is no control that could widen it, here or at the API.
  //
  // Read once, deliberately: from here on these are the user's ticks, and a re-run of `load`
  // must not reach in and re-tick a box they just cleared on a consent screen.
  // svelte-ignore state_referenced_locally
  let selected = $state<string[]>(data.consent.scopes.map((s) => s.value));

  const reads = $derived(data.consent.scopes.filter((s) => s.read));
  const writes = $derived(data.consent.scopes.filter((s) => !s.read));

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
      {#each selected as scope (scope)}
        <input type="hidden" name="scopes" value={scope} />
      {/each}

      {#each [{ list: reads, key: "read" }, { list: writes, key: "write" }] as group (group.key)}
        {#if group.list.length > 0}
          <div class="mt-4">
            <div class="mb-1 flex items-center justify-between">
              <span class="text-xs font-semibold tracking-wide text-text uppercase">
                {t(`oauth.consent.group_${group.key}`)}
              </span>
              <button
                type="button"
                class="text-xs text-brand hover:underline"
                onclick={() =>
                  toggleGroup(group.list, !group.list.every((s) => selected.includes(s.value)))}
              >
                {group.list.every((s) => selected.includes(s.value))
                  ? t("oauth.consent.none")
                  : t("oauth.consent.all")}
              </button>
            </div>
            <div class="max-h-48 space-y-1 overflow-y-auto rounded-lg border border-border p-2">
              {#each group.list as scope (scope.value)}
                <label class="flex items-center gap-2 text-xs text-text">
                  <input
                    type="checkbox"
                    checked={selected.includes(scope.value)}
                    onchange={() => toggle(scope.value)}
                    class="h-3.5 w-3.5 rounded border-border"
                  />
                  <span>{t(scope.label_key)}</span>
                  {#if scope.value.includes(":")}
                    <span class="text-text-muted/70">({scope.value.split(":")[1]})</span>
                  {/if}
                </label>
              {/each}
            </div>
          </div>
        {/if}
      {/each}

      <p class="mt-3 flex items-start gap-2 text-xs text-text-muted">
        <ShieldCheck size={14} class="mt-0.5 shrink-0" />
        <span>{t("oauth.consent.cap")}</span>
      </p>

      {#if form?.error}
        <p class="mt-3 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
      {/if}

      <div class="mt-5 flex items-center gap-2">
        <Button loading={busy.is("approve")} disabled={selected.length === 0}>
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
