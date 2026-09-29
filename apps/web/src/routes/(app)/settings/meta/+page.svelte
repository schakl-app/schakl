<script lang="ts">
  /**
   * Instellingen → Meta.
   *
   * **The setup is the hard part, so the screen is the guide.** Connecting Meta without its
   * review means an app of the agency's own, a system user in the agency's own Business
   * portfolio, and clients sharing their assets with that portfolio — five steps across three
   * of Meta's screens, none of which explains the others. So the steps are written here, in
   * order, each ticked from what this instance can *see* rather than from a box somebody
   * checked: a step reads as done when it is.
   *
   * **A token's clock is on the row.** When it expires, when it will be refreshed by itself,
   * and what the last refresh said — because a token that runs out silently is every client's
   * publishing stopping on a date nobody wrote down.
   */
  import Check from "@lucide/svelte/icons/check";
  import ExternalLink from "@lucide/svelte/icons/external-link";
  import KeyRound from "@lucide/svelte/icons/key-round";
  import RefreshCw from "@lucide/svelte/icons/refresh-cw";
  import Trash2 from "@lucide/svelte/icons/trash-2";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";

  import { enhance } from "$app/forms";
  import { fmtDateTime, fmtDayMonthYear } from "$lib/core/format";
  import { t, tn } from "$lib/core/i18n";
  import { stateFillClass } from "$lib/core/state";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import ActionsMenu, { type ActionItem } from "$lib/core/ui/ActionsMenu.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import CopyBlock from "$lib/core/ui/CopyBlock.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import StateMark from "$lib/core/ui/StateMark.svelte";
  import {
    capabilityLabel,
    credentialState,
    errorText,
    missingCapabilities,
  } from "$lib/integrations/meta/format";
  import type { MetaCredential } from "$lib/integrations/meta/types";

  let { data, form } = $props();

  const busy = new InFlight();
  const settings = $derived(data.settings);
  const credentials = $derived(data.credentials);
  const status = $derived(data.status);

  const inputClass =
    "w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";

  // --- the guide -----------------------------------------------------------------------------
  const appDone = $derived(Boolean(settings?.app_id && settings.app_secret_configured));
  const tokenDone = $derived(credentials.some((c) => c.active && c.status === "active"));
  const found = $derived((status?.channels_linked ?? 0) + (status?.channels_unlinked ?? 0) > 0);
  const linkedDone = $derived((status?.channels_linked ?? 0) > 0);
  const allDone = $derived(appDone && tokenDone && linkedDone);
  const businessId = $derived(credentials.find((c) => c.business_id)?.business_id ?? "");
  const scopes = $derived((settings?.recommended_scopes ?? []).join(", "));

  // --- a token row ---------------------------------------------------------------------------
  let replacing = $state<MetaCredential | null>(null);
  let replaceOpen = $state(false);
  let removing = $state<MetaCredential | null>(null);
  let removeOpen = $state(false);
  let rowForm = $state<HTMLFormElement | null>(null);
  let rowAction = $state("?/verify");
  let rowId = $state("");

  async function run(action: string, credential: MetaCredential): Promise<void> {
    rowAction = action;
    rowId = credential.id;
    // The two fields are state the form renders from, so it is posted a tick later.
    await Promise.resolve();
    rowForm?.requestSubmit();
  }

  function actions(credential: MetaCredential): ActionItem[] {
    const items: ActionItem[] = [
      {
        label: t("settings.meta.token.verify"),
        icon: Check,
        onclick: () => run("?/verify", credential),
      },
      {
        label: t("settings.meta.token.discover"),
        icon: RefreshCw,
        onclick: () => run("?/discover", credential),
      },
    ];
    if (credential.expires_at && credential.status !== "expired") {
      items.push({
        label: t("settings.meta.token.refresh_now"),
        icon: RefreshCw,
        onclick: () => run("?/refresh", credential),
      });
    }
    items.push({
      label: t("settings.meta.token.replace"),
      icon: KeyRound,
      onclick: () => {
        replacing = credential;
        replaceOpen = true;
      },
    });
    items.push({
      label: t("common.delete"),
      icon: Trash2,
      danger: true,
      onclick: () => {
        removing = credential;
        removeOpen = true;
      },
    });
    return items;
  }

  function expiry(credential: MetaCredential): string {
    if (credential.status === "expired") return t("settings.meta.token.expired");
    if (!credential.expires_at) {
      return credential.last_verified_at
        ? t("settings.meta.token.never_expires")
        : t("settings.meta.token.unknown_expiry");
    }
    return tn("settings.meta.token.expires", credential.days_left ?? 0, {
      date: fmtDayMonthYear(credential.expires_at.slice(0, 10)),
      days: String(credential.days_left ?? 0),
    });
  }

  function refreshLine(credential: MetaCredential): string | null {
    if (credential.status === "expired" || !credential.expires_at) return null;
    if (credential.refresh_error) return null;
    if (!settings?.app_secret_configured) return t("settings.meta.token.refresh_needs_app");
    if (credential.refreshed_at && (credential.days_left ?? 0) > 20) {
      return t("settings.meta.token.refreshed", { when: fmtDateTime(credential.refreshed_at) });
    }
    if ((credential.days_left ?? 99) <= 20) return t("settings.meta.token.refresh_tonight");
    if (credential.refresh_due_at) {
      return t("settings.meta.token.refresh_due", {
        date: fmtDayMonthYear(credential.refresh_due_at.slice(0, 10)),
      });
    }
    return null;
  }

  const outcome = $derived.by(() => {
    if (!form || !("saved" in form)) return null;
    switch (form.saved) {
      case "discovered":
        return tn("settings.meta.discovered", Number(form.found ?? 0), {
          count: String(form.found ?? 0),
          created: String(form.created ?? 0),
        });
      case "token":
        return form.found != null
          ? tn("settings.meta.token_added_found", Number(form.found), {
              count: String(form.found),
            })
          : t("settings.meta.token_saved");
      case "refreshed":
        return t("settings.meta.refreshed");
      case "refresh_failed":
        return t("settings.meta.refresh_failed");
      case "verified":
        return t("settings.meta.verified");
      case "removed":
        return t("settings.meta.removed");
      default:
        return t("common.saved");
    }
  });
  const warnings = $derived(
    form && "warnings" in form && Array.isArray(form.warnings) ? (form.warnings as string[]) : [],
  );
  const refused = (scope: string) =>
    form && "error" in form && form.error && (!("scope" in form) || form.scope === scope)
      ? form
      : null;
</script>

<svelte:head>
  <title>{pageTitle(t("settings.meta.title"))}</title>
</svelte:head>

<h1 class="mb-1 mt-2 text-xl font-semibold text-text">{t("settings.meta.title")}</h1>
<p class="mb-6 max-w-2xl text-sm text-text-muted">{t("settings.meta.subtitle")}</p>

{#if data.loadError}
  <p class="mb-4 text-sm text-red-600 dark:text-red-400" role="alert">{t(data.loadError)}</p>
{/if}

{#if outcome}
  <div class="mb-4 max-w-2xl rounded-lg bg-surface-tint px-4 py-3 text-sm text-text" role="status">
    <p>{outcome}</p>
    {#each warnings as warning (warning)}
      <p class="mt-1 text-text-muted">{t(warning)}</p>
    {/each}
    {#if form && "found" in form && Number(form.found) > 0}
      <a href="/marketing/social/channels" class="mt-1 inline-block text-brand hover:underline">
        {t("settings.meta.go_link")}
      </a>
    {/if}
  </div>
{/if}

<!-- The guide -->
<details class="mb-6 max-w-2xl rounded-xl border border-border bg-surface-raised" open={!allDone}>
  <summary class="cursor-pointer px-5 py-4 text-sm font-semibold text-text">
    {t("settings.meta.guide.title")}
    <span class="ml-2 font-normal text-text-muted">
      {allDone ? t("settings.meta.guide.done") : t("settings.meta.guide.why")}
    </span>
  </summary>
  <ol class="space-y-5 border-t border-border px-5 py-5">
    {#snippet step(number: number, done: boolean, title: string)}
      <span
        class="flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold
          {done ? stateFillClass('ok') + ' text-white' : 'border border-border text-text-muted'}"
        aria-hidden="true"
      >
        {#if done}<Check size={14} />{:else}{number}{/if}
      </span>
      <span class="sr-only">{done ? t("settings.meta.guide.step_done") : ""}</span>
      <span class="text-sm font-medium text-text">{title}</span>
    {/snippet}

    <li>
      <div class="flex items-center gap-2.5">
        {@render step(1, appDone, t("settings.meta.guide.app_title"))}
      </div>
      <div class="ml-8 mt-1.5 space-y-2 text-sm text-text-muted">
        <p>{t("settings.meta.guide.app_body")}</p>
        <a
          href="https://developers.facebook.com/apps/"
          target="_blank"
          rel="noopener noreferrer"
          class="inline-flex items-center gap-1 text-brand hover:underline"
        >
          developers.facebook.com/apps
          <ExternalLink size={13} aria-hidden="true" />
        </a>
      </div>
    </li>

    <li>
      <div class="flex items-center gap-2.5">
        {@render step(2, tokenDone, t("settings.meta.guide.token_title"))}
      </div>
      <div class="ml-8 mt-1.5 space-y-2 text-sm text-text-muted">
        <p>{t("settings.meta.guide.token_body")}</p>
        {#if scopes}
          <CopyBlock
            value={scopes}
            label={t("settings.meta.guide.scopes")}
            help={t("settings.meta.guide.scopes_help")}
          />
        {/if}
      </div>
    </li>

    <li>
      <div class="flex items-center gap-2.5">
        {@render step(3, found, t("settings.meta.guide.share_title"))}
      </div>
      <div class="ml-8 mt-1.5 space-y-2 text-sm text-text-muted">
        <p>{t("settings.meta.guide.share_body")}</p>
        {#if businessId}
          <CopyBlock
            value={businessId}
            label={t("settings.meta.guide.business_id")}
            help={t("settings.meta.guide.business_id_help")}
          />
        {/if}
      </div>
    </li>

    <li>
      <div class="flex items-center gap-2.5">
        {@render step(4, linkedDone, t("settings.meta.guide.link_title"))}
      </div>
      <div class="ml-8 mt-1.5 space-y-2 text-sm text-text-muted">
        <p>{t("settings.meta.guide.link_body")}</p>
        {#if found}
          <a href="/marketing/social/channels" class="text-brand hover:underline">
            {(status?.channels_unlinked ?? 0) > 0
              ? tn("settings.meta.guide.link_waiting", status?.channels_unlinked ?? 0, {
                  count: String(status?.channels_unlinked ?? 0),
                })
              : t("settings.meta.go_link")}
          </a>
        {/if}
      </div>
    </li>

    <li>
      <div class="flex items-center gap-2.5">
        <span
          class="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-border text-xs font-semibold text-text-muted"
          aria-hidden="true">i</span
        >
        <span class="text-sm font-medium text-text">{t("settings.meta.guide.live_title")}</span>
      </div>
      <div class="ml-8 mt-1.5 space-y-2 text-sm text-text-muted">
        <p>{t("settings.meta.guide.live_body")}</p>
        {#if settings?.media_url_prefix}
          <CopyBlock
            value={settings.media_url_prefix}
            label={t("settings.meta.guide.media_url")}
            help={t("settings.meta.guide.media_url_help")}
          />
        {/if}
      </div>
    </li>
  </ol>
</details>

<!-- The app -->
<section class="max-w-2xl rounded-xl border border-border bg-surface-raised p-5">
  <h2 class="mb-1 text-sm font-semibold text-text">{t("settings.meta.app.title")}</h2>
  <p class="mb-4 text-xs text-text-muted">{t("settings.meta.app.hint")}</p>
  <!-- This edits settings that exist, so nothing is reset, with one exception: a secret
       that was just stored does not stay on the screen it was typed into. -->
  <form
    method="POST"
    action="?/saveApp"
    use:enhance={busy.wrap("app", () => async ({ result, update, formElement }) => {
      await update({ reset: false });
      if (result.type === "success") {
        const secret = formElement.querySelector<HTMLInputElement>('[name="app_secret"]');
        if (secret) secret.value = "";
      }
    })}
    class="space-y-4"
  >
    <div>
      <label for="meta-app-id" class="mb-1 block text-sm font-medium text-text">
        {t("settings.meta.app.id")}
      </label>
      <input
        id="meta-app-id"
        name="app_id"
        inputmode="numeric"
        autocomplete="off"
        value={settings?.app_id ?? ""}
        class={inputClass}
      />
      {#if refused("app")?.fields?.app_id}
        <p class="mt-1 text-sm text-red-600 dark:text-red-400" role="alert">
          {t(refused("app")!.fields!.app_id)}
        </p>
      {/if}
    </div>
    <div>
      <label for="meta-app-secret" class="mb-1 block text-sm font-medium text-text">
        {t("settings.meta.app.secret")}
      </label>
      <input
        id="meta-app-secret"
        name="app_secret"
        type="password"
        autocomplete="new-password"
        placeholder={settings?.app_secret_configured ? t("settings.meta.app.secret_set") : ""}
        class={inputClass}
      />
      <p class="mt-1 text-xs text-text-muted">{t("settings.meta.app.secret_hint")}</p>
    </div>
    {#if refused("app") && !refused("app")?.fields?.app_id}
      <p class="text-sm text-red-600 dark:text-red-400" role="alert">{t(refused("app")!.error)}</p>
    {/if}
    <div class="flex flex-wrap items-center gap-3 border-t border-border pt-4">
      <Button type="submit" loading={busy.is("app")} disabled={busy.active}>
        {t("common.save")}
      </Button>
      {#if settings?.app_secret_configured}
        <Button type="submit" formaction="?/clearSecret" variant="secondary" disabled={busy.active}>
          {t("settings.meta.app.clear_secret")}
        </Button>
      {/if}
    </div>
  </form>
</section>

<!-- The tokens -->
<section class="mt-6 max-w-2xl rounded-xl border border-border bg-surface-raised p-5">
  <h2 class="mb-1 text-sm font-semibold text-text">{t("settings.meta.token.title")}</h2>
  <p class="mb-4 text-xs text-text-muted">{t("settings.meta.token.hint")}</p>

  {#if credentials.length === 0}
    <p class="mb-5 text-sm text-text-muted">{t("settings.meta.token.none")}</p>
  {:else}
    <ul class="mb-5 divide-y divide-border">
      {#each credentials as credential (credential.id)}
        {@const missing = missingCapabilities(credential)}
        <li class="flex items-start gap-3 py-4 first:pt-0">
          <div class="min-w-0 flex-1 space-y-1.5">
            <p class="flex flex-wrap items-center gap-2">
              <span class="truncate text-sm font-medium text-text">{credential.label}</span>
              <StateMark
                state={credentialState(credential)}
                label={t(`settings.meta.token.status.${credential.status}`)}
                variant="chip"
              />
            </p>
            <p class="truncate text-xs text-text-muted">
              {[credential.business_name, credential.subject_name].filter(Boolean).join(" · ") ||
                t("settings.meta.token.unverified")}
            </p>

            <p class="text-sm text-text">{expiry(credential)}</p>
            {#if refreshLine(credential)}
              <p class="text-xs text-text-muted">{refreshLine(credential)}</p>
            {/if}
            {#if credential.refresh_error}
              <p class="flex items-start gap-1.5 text-sm text-text">
                <TriangleAlert
                  size={14}
                  class="mt-0.5 shrink-0 text-amber-600 dark:text-amber-400"
                  aria-hidden="true"
                />
                <span>
                  {t("settings.meta.token.refresh_error")}
                  <span class="text-text-muted">{errorText(credential.refresh_error)}</span>
                </span>
              </p>
            {/if}
            {#if credential.last_error}
              <p class="break-words text-sm text-text">{errorText(credential.last_error)}</p>
            {/if}

            {#if credential.token_kind && credential.token_kind !== "SYSTEM_USER"}
              <p class="text-xs text-text-muted">{t("settings.meta.token.personal")}</p>
            {/if}
            {#if credential.app_matches === false}
              <p class="text-xs text-text-muted">{t("settings.meta.token.other_app")}</p>
            {/if}
            {#if missing.length > 0}
              <div class="text-xs text-text-muted">
                <p>{t("settings.meta.token.cannot")}</p>
                <ul class="ml-4 mt-0.5 list-disc">
                  {#each missing as name (name)}<li>{capabilityLabel(name)}</li>{/each}
                </ul>
                <p class="mt-1">
                  {t("settings.meta.token.missing_scopes", {
                    scopes: credential.missing_scopes.join(", "),
                  })}
                </p>
              </div>
            {/if}

            <p class="text-xs text-text-muted">
              {#if credential.asset_count > 0}
                <a href="/marketing/social/channels?state=all" class="hover:text-brand">
                  {t("settings.meta.token.assets", {
                    found: String(credential.asset_count),
                    linked: String(credential.linked_asset_count),
                  })}
                </a>
              {:else if credential.last_discovered_at}
                {t("settings.meta.token.no_assets")}
              {/if}
              {#if credential.last_verified_at}
                · {t("settings.meta.token.checked", {
                  when: fmtDateTime(credential.last_verified_at),
                })}
              {/if}
            </p>
          </div>
          <ActionsMenu items={actions(credential)} />
        </li>
      {/each}
    </ul>
  {/if}

  <!-- One form for the row actions, posted by the ⋯. clear(): each starts something. -->
  <form
    bind:this={rowForm}
    method="POST"
    action={rowAction}
    use:enhance={busy.clear("row")}
    class="hidden"
  >
    <input type="hidden" name="credential_id" value={rowId} />
  </form>
  {#if busy.is("row")}
    <p class="mb-4 text-sm text-text-muted" role="status">{t("settings.meta.token.asking")}</p>
  {/if}

  <!-- clear(): a create form starts something new. -->
  <form
    method="POST"
    action="?/addToken"
    use:enhance={busy.clear("token")}
    class="space-y-3 border-t border-border pt-4"
  >
    <h3 class="text-sm font-medium text-text">{t("settings.meta.token.add")}</h3>
    {#if !appDone}
      <p class="text-xs text-text-muted">{t("settings.meta.token.app_first")}</p>
    {/if}
    <div class="grid gap-3 sm:grid-cols-2">
      <div>
        <label for="meta-token-label" class="mb-1 block text-sm font-medium text-text">
          {t("settings.meta.token.label")}
        </label>
        <input
          id="meta-token-label"
          name="label"
          required
          maxlength="120"
          placeholder={t("settings.meta.token.label_placeholder")}
          class={inputClass}
        />
        {#if refused("token")?.fields?.label}
          <p class="mt-1 text-sm text-red-600 dark:text-red-400" role="alert">
            {t(refused("token")!.fields!.label)}
          </p>
        {/if}
      </div>
      <div>
        <label for="meta-token-business" class="mb-1 block text-sm font-medium text-text">
          {t("settings.meta.token.business_id")}
        </label>
        <input
          id="meta-token-business"
          name="business_id"
          inputmode="numeric"
          autocomplete="off"
          class={inputClass}
        />
        {#if refused("token")?.fields?.business_id}
          <p class="mt-1 text-sm text-red-600 dark:text-red-400" role="alert">
            {t(refused("token")!.fields!.business_id)}
          </p>
        {/if}
      </div>
    </div>
    <p class="text-xs text-text-muted">{t("settings.meta.token.business_id_hint")}</p>
    <div>
      <label for="meta-token" class="mb-1 block text-sm font-medium text-text">
        {t("settings.meta.token.token")}
      </label>
      <input
        id="meta-token"
        name="token"
        type="password"
        required
        autocomplete="new-password"
        class={inputClass}
      />
      <p class="mt-1 text-xs text-text-muted">{t("settings.meta.token.token_hint")}</p>
    </div>
    {#if refused("token") && !refused("token")?.fields}
      <p class="text-sm text-red-600 dark:text-red-400" role="alert">
        {t(refused("token")!.error)}
      </p>
    {/if}
    <Button type="submit" loading={busy.is("token")} disabled={busy.active}>
      {t("settings.meta.token.add_submit")}
    </Button>
  </form>
</section>

<!-- Publishing -->
<section class="mt-6 max-w-2xl rounded-xl border border-border bg-surface-raised p-5">
  <h2 class="mb-4 text-sm font-semibold text-text">{t("settings.meta.publishing.title")}</h2>
  <form
    method="POST"
    action="?/savePublishing"
    use:enhance={busy.keep("publishing")}
    class="space-y-5"
  >
    <fieldset>
      <legend class="mb-1 text-sm font-medium text-text">
        {t("settings.meta.publishing.scheduler")}
      </legend>
      <p class="mb-3 text-xs text-text-muted">{t("settings.meta.publishing.scheduler_hint")}</p>
      <div class="space-y-3">
        <label class="flex items-start gap-2.5 text-sm text-text">
          <input
            type="radio"
            name="facebook_scheduler"
            value="schakl"
            class="mt-0.5"
            checked={(settings?.facebook_scheduler ?? "schakl") === "schakl"}
          />
          <span>
            {t("settings.meta.publishing.own")}
            <span class="mt-0.5 block text-xs text-text-muted">
              {t("settings.meta.publishing.own_hint")}
            </span>
          </span>
        </label>
        <label class="flex items-start gap-2.5 text-sm text-text">
          <input
            type="radio"
            name="facebook_scheduler"
            value="meta"
            class="mt-0.5"
            checked={settings?.facebook_scheduler === "meta"}
          />
          <span>
            {t("settings.meta.publishing.meta")}
            <span class="mt-0.5 block text-xs text-text-muted">
              {t("settings.meta.publishing.meta_hint")}
            </span>
          </span>
        </label>
      </div>
      <p class="mt-3 text-xs text-text-muted">{t("settings.meta.publishing.instagram")}</p>
    </fieldset>

    <div class="flex items-start gap-2 border-t border-border pt-5">
      <input
        id="meta-writes-enabled"
        name="writes_enabled"
        type="checkbox"
        value="true"
        checked={settings?.writes_enabled ?? true}
        class="mt-0.5"
      />
      <label for="meta-writes-enabled" class="text-sm text-text">
        {t("settings.meta.publishing.writes_enabled")}
        <span class="mt-0.5 block text-xs text-text-muted">
          {t("settings.meta.publishing.writes_enabled_hint")}
        </span>
      </label>
    </div>

    {#if data.mayPolicy}
      <input type="hidden" name="ads_present" value="1" />
      <div class="flex items-start gap-2">
        <input
          id="meta-ads-writes-enabled"
          name="ads_writes_enabled"
          type="checkbox"
          value="true"
          checked={data.adsSettings?.writes_enabled ?? true}
          class="mt-0.5"
        />
        <label for="meta-ads-writes-enabled" class="text-sm text-text">
          {t("settings.meta.publishing.ads_writes_enabled")}
          <span class="mt-0.5 block text-xs text-text-muted">
            {t("settings.meta.publishing.ads_writes_enabled_hint")}
          </span>
        </label>
      </div>
    {/if}

    {#if refused("publishing")}
      <p class="text-sm text-red-600 dark:text-red-400" role="alert">
        {t(refused("publishing")!.error)}
      </p>
    {/if}
    <div class="flex items-center gap-3 border-t border-border pt-4">
      <Button type="submit" loading={busy.is("publishing")} disabled={busy.active}>
        {t("common.save")}
      </Button>
      <span class="text-xs text-text-muted">
        {t("settings.meta.publishing.version", { version: settings?.api_version ?? "" })}
      </span>
    </div>
  </form>
</section>

<Modal
  bind:open={replaceOpen}
  title={t("settings.meta.token.replace_title", { label: replacing?.label ?? "" })}
>
  <form
    method="POST"
    action="?/replaceToken"
    use:enhance={busy.wrap("replace", () => async ({ result, update }) => {
      await update({ reset: true });
      if (result.type === "success") replaceOpen = false;
    })}
    class="space-y-4"
  >
    <input type="hidden" name="credential_id" value={replacing?.id ?? ""} />
    <p class="text-sm text-text-muted">{t("settings.meta.token.replace_hint")}</p>
    <div>
      <label for="meta-replace-label" class="mb-1 block text-sm font-medium text-text">
        {t("settings.meta.token.label")}
      </label>
      <input
        id="meta-replace-label"
        name="label"
        maxlength="120"
        value={replacing?.label ?? ""}
        class={inputClass}
      />
    </div>
    <div>
      <label for="meta-replace-business" class="mb-1 block text-sm font-medium text-text">
        {t("settings.meta.token.business_id")}
      </label>
      <input
        id="meta-replace-business"
        name="business_id"
        inputmode="numeric"
        value={replacing?.business_id ?? ""}
        class={inputClass}
      />
    </div>
    <div>
      <label for="meta-replace-token" class="mb-1 block text-sm font-medium text-text">
        {t("settings.meta.token.token")}
      </label>
      <input
        id="meta-replace-token"
        name="token"
        type="password"
        autocomplete="new-password"
        placeholder={t("settings.meta.token.keep_placeholder")}
        class={inputClass}
      />
    </div>
    {#if refused("token")}
      <p class="text-sm text-red-600 dark:text-red-400" role="alert">
        {t(Object.values(refused("token")!.fields ?? {})[0] ?? refused("token")!.error)}
      </p>
    {/if}
    <div class="flex justify-end gap-2 border-t border-border pt-4">
      <Button type="button" variant="secondary" onclick={() => (replaceOpen = false)}>
        {t("common.cancel")}
      </Button>
      <Button type="submit" loading={busy.is("replace")} disabled={busy.active}>
        {t("common.save")}
      </Button>
    </div>
  </form>
</Modal>

<ConfirmDialog
  bind:open={removeOpen}
  title={t("settings.meta.token.remove_title", { label: removing?.label ?? "" })}
  message={t("settings.meta.token.remove_message")}
  consequences={[
    t("settings.meta.token.remove_stops", {
      count: String(removing?.linked_asset_count ?? 0),
    }),
    t("settings.meta.token.remove_keeps"),
    t("settings.meta.token.remove_not_revoked"),
  ]}
  action="?/remove"
  fields={{ credential_id: removing?.id ?? "" }}
/>
