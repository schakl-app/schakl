<script lang="ts">
  /**
   * One source's account picker (issue #132). Options come from Google, so they load lazily when
   * the picker mounts (in the panel's edit mode) via the `/marketing/accounts` proxy, never on the
   * company page's render. An empty list *teaches*: no connection → connect; missing scope →
   * reconnect; Ads with no developer token → say so. Selecting an account posts to the host page's
   * `?/marketingLink` action. **No inline-create** — these are external resources, the documented
   * picker exception (docs/UX.md), like invited employees.
   */
  import { enhance } from "$app/forms";

  import { t } from "$lib/core/i18n";
  import Combobox from "$lib/core/ui/Combobox.svelte";

  import { connectHref, type AccountsResponse, type MarketingSource } from "./types";

  let {
    source,
    linkedIds,
  }: {
    source: MarketingSource;
    /** external_ids already linked to this company for this source — filtered out of options. */
    linkedIds: string[];
  } = $props();

  let loading = $state(true);
  let response = $state<AccountsResponse | null>(null);
  let value = $state("");
  let form: HTMLFormElement | undefined = $state();
  let picked = $state<{ external_id: string; display_name: string; config: string } | null>(null);

  async function load() {
    loading = true;
    try {
      const res = await fetch(`/marketing/accounts?source=${source}`);
      response = (await res.json()) as AccountsResponse;
    } catch {
      response = {
        source,
        connected: false,
        has_scope: false,
        configured: true,
        accounts: [],
        error: "marketing.accounts_error",
        connect_flag: "",
      };
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    void load();
  });

  const linked = $derived(new Set(linkedIds));
  const items = $derived(
    (response?.accounts ?? [])
      .filter((a) => !linked.has(a.external_id))
      .map((a) => ({
        value: a.external_id,
        label: a.display_name,
        hint: a.account_hint ?? undefined,
      })),
  );

  function choose(externalId: string) {
    const account = response?.accounts.find((a) => a.external_id === externalId);
    if (!account) return;
    picked = {
      external_id: account.external_id,
      display_name: account.display_name,
      config: JSON.stringify(account.config ?? {}),
    };
    // Wait for the hidden inputs to reflect `picked`, then submit to the host action.
    queueMicrotask(() => form?.requestSubmit());
  }
</script>

<div class="space-y-1.5">
  <span class="text-xs font-medium text-text-muted">{t(`marketing.picker.${source}`)}</span>
  {#if loading}
    <p class="text-sm text-text-muted">{t("marketing.picker.loading")}</p>
  {:else if response && !response.connected}
    <p class="text-sm text-text-muted">
      {t("marketing.empty.needs_connection")}
    </p>
    <a href={connectHref([source])} class="text-sm font-medium text-brand hover:underline">
      {t("marketing.connect_cta")}
    </a>
  {:else if response && !response.has_scope}
    <p class="text-sm text-text-muted">
      {t("marketing.no_scope", { source: t(`marketing.source.${source}`) })}
    </p>
    <a href={connectHref([source])} class="text-sm font-medium text-brand hover:underline">
      {t("marketing.reconnect")}
    </a>
  {:else if response && !response.configured}
    <p class="text-sm text-text-muted">{t("marketing.ads_not_configured")}</p>
  {:else if response?.error}
    <p class="text-sm text-red-600 dark:text-red-400">{t(response.error)}</p>
    <a href={connectHref([source])} class="text-sm font-medium text-brand hover:underline">
      {t("marketing.reconnect")}
    </a>
  {:else if items.length === 0}
    <p class="text-sm text-text-muted">{t("marketing.picker.none")}</p>
  {:else}
    <Combobox
      {items}
      name="_marketing_pick_{source}"
      id="marketing-pick-{source}"
      bind:value
      allowEmpty={false}
      placeholder={t("marketing.picker.placeholder")}
      onselect={choose}
    />
  {/if}

  <!-- Submitted programmatically on select; posts to the company page's ?/marketingLink action. -->
  <form
    bind:this={form}
    method="POST"
    action="?/marketingLink"
    use:enhance={() =>
      ({ update }) => {
        value = "";
        picked = null;
        return update();
      }}
    class="hidden"
  >
    <input type="hidden" name="source" value={source} />
    <input type="hidden" name="external_id" value={picked?.external_id ?? ""} />
    <input type="hidden" name="display_name" value={picked?.display_name ?? ""} />
    <input type="hidden" name="config" value={picked?.config ?? "{}"} />
  </form>
</div>
