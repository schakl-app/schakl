<script lang="ts">
  /**
   * One container: what is staged, what is live, and what schakl set up.
   *
   * The page is arranged the way the work is done — first the conversions (what an agency is
   * actually asked for), then the raw contents, then the versions, because publishing is the
   * last thing you do and the only thing here with an audience outside the building. Every write
   * control mirrors the API key its own call makes (#310), never the read permission the page is
   * about.
   */
  import { enhance } from "$app/forms";
  import { AlertTriangle, ExternalLink, Tags } from "@lucide/svelte";

  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import Button from "$lib/core/ui/Button.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";

  let { data, form } = $props();
  const container = $derived(data.container);
  const busy = new InFlight();

  let adding = $state(false);
  let kind = $state("ga4_event");
  let triggerKind = $state("form_submit");

  const companyName = $derived(
    container.company_id
      ? (data.companies.find((c) => c.id === container.company_id)?.name ?? "")
      : "",
  );

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";
  const selectClass = `${inputClass} bg-surface-raised`;

  const TRIGGER_KINDS = [
    "form_submit",
    "page_view",
    "link_click",
    "element_click",
    "element_visibility",
    "custom_event",
  ];
</script>

<svelte:head>
  <title>{pageTitle(container.name || container.public_id)}</title>
</svelte:head>

<div class="mb-4 flex flex-wrap items-start justify-between gap-4">
  <div class="min-w-0">
    <a href="/marketing/tag-manager" class="text-xs text-text-muted hover:underline">
      {t("nav.gtm")}
    </a>
    <h1 class="mt-1 flex items-center gap-2 text-xl font-semibold text-text">
      <Tags size={20} class="shrink-0 text-text-muted" aria-hidden="true" />
      {container.name || container.public_id}
    </h1>
    <p class="mt-1 text-sm text-text-muted">
      {container.public_id}
      {#if companyName}· {companyName}{/if}
      {#if container.usage_context?.length}· {container.usage_context.join(", ")}{/if}
    </p>
  </div>
  <div class="flex shrink-0 items-center gap-2">
    <form method="POST" action="?/verify" use:enhance={busy.clear("verify")}>
      <Button type="submit" variant="secondary" size="sm" disabled={busy.active}>
        {t("gtm.verify")}
      </Button>
    </form>
    <a
      href={container.tag_manager_url}
      target="_blank"
      rel="noreferrer"
      class="inline-flex items-center gap-1 rounded-lg border border-border px-3 py-1.5 text-sm text-text hover:bg-surface"
    >
      {t("gtm.open_in_gtm")}
      <ExternalLink size={14} aria-hidden="true" />
    </a>
  </div>
</div>

{#if form?.error}
  <p class="mb-4 text-sm text-text">{t(form.error)}</p>
{/if}
{#if form?.conversionCreated}
  <p class="mb-4 text-sm text-text">
    {t("gtm.conversions.created", { name: form.conversionCreated })}
  </p>
{/if}
{#if form?.versionEmpty}
  <p class="mb-4 text-sm text-text">{t("gtm.versions.empty_workspace")}</p>
{/if}
{#if form?.versionCreated}
  <p class="mb-4 text-sm text-text">
    {t("gtm.versions.created", { version: form.versionCreated })}
  </p>
{/if}
{#if form?.published}
  <p class="mb-4 text-sm text-text">{t("gtm.versions.published", { version: form.published })}</p>
{/if}

{#if container.status === "error" && container.last_error}
  <!-- Google's own sentence, verbatim: it is the one thing that says *what* to fix. -->
  <p class="mb-4 flex items-start gap-2 text-sm text-text">
    <AlertTriangle size={16} class="mt-0.5 shrink-0" aria-hidden="true" />
    <span class="min-w-0 break-words">{container.last_error}</span>
  </p>
{/if}
{#if data.liveError}
  <!-- A refusal on one live read blanks that section only; saying so beats five empty lists
       that look exactly like an empty container. -->
  <p class="mb-4 flex items-start gap-2 text-sm text-text">
    <AlertTriangle size={16} class="mt-0.5 shrink-0" aria-hidden="true" />
    <span>{t(data.liveError)}</span>
  </p>
{/if}

<!-- Overview -->
<section class="mb-6 rounded-xl border border-border bg-surface-raised p-5">
  <dl class="grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
    <div>
      <dt class="text-xs text-text-muted">{t("gtm.live_version")}</dt>
      <dd class="mt-0.5 text-text">
        {container.live_version_id
          ? `${container.live_version_id}${container.live_version_name ? ` · ${container.live_version_name}` : ""}`
          : t("gtm.no_live_version")}
      </dd>
    </div>
    <div>
      <dt class="text-xs text-text-muted">{t("gtm.tag_count")}</dt>
      <dd class="mt-0.5 text-text">{container.tag_count}</dd>
    </div>
    <div>
      <dt class="text-xs text-text-muted">{t("gtm.trigger_count")}</dt>
      <dd class="mt-0.5 text-text">{container.trigger_count}</dd>
    </div>
    <div>
      <dt class="text-xs text-text-muted">{t("gtm.staged")}</dt>
      <dd class="mt-0.5 text-text">
        {data.status && data.status.changes > 0 ? data.status.changes : t("gtm.staged_none")}
      </dd>
    </div>
  </dl>
  <p class="mt-4 border-t border-border pt-3 text-xs text-text-muted">
    {t("gtm.detail.workspace_hint")}
    {#if container.observed_at}
      · {t("gtm.checked", { when: fmtDateTime(container.observed_at) })}
    {/if}
  </p>
</section>

<!-- Conversions: what an agency is actually asked for -->
<section class="mb-6 rounded-xl border border-border bg-surface-raised p-5">
  <div class="mb-3 flex items-start justify-between gap-4">
    <div>
      <h2 class="text-sm font-semibold text-text">{t("gtm.conversions.title")}</h2>
      <p class="mt-0.5 text-xs text-text-muted">{t("gtm.conversions.subtitle")}</p>
    </div>
    {#if data.canWrite}
      <Button type="button" size="sm" onclick={() => (adding = true)}>
        {t("gtm.conversions.add")}
      </Button>
    {/if}
  </div>
  {#if data.conversions.length === 0}
    <p class="text-sm text-text-muted">{t("gtm.conversions.empty")}</p>
  {:else}
    <ul class="divide-y divide-border">
      {#each data.conversions as conversion (conversion.id)}
        <li class="py-2">
          <span class="block text-sm font-medium text-text">{conversion.name}</span>
          <span class="mt-0.5 block text-xs text-text-muted">
            {t(`gtm.conversions.kind.${conversion.kind}`)}
            · {t(`gtm.conversions.status.${conversion.status}`)}
            {#if conversion.created_by_name}· {conversion.created_by_name}{/if}
            · {fmtDateTime(conversion.created_at)}
          </span>
          {#if conversion.last_error}
            <span class="mt-1 block break-words text-xs text-text">{conversion.last_error}</span>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
</section>

<div class="grid gap-6 lg:grid-cols-2">
  <!-- Tags -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="mb-3 text-sm font-semibold text-text">{t("gtm.tags.title")}</h2>
    {#if data.tags.length === 0}
      <p class="text-sm text-text-muted">{t("gtm.tags.empty")}</p>
    {:else}
      <ul class="divide-y divide-border">
        {#each data.tags as tag (tag.tag_id)}
          <li class="flex items-start gap-3 py-2">
            <div class="min-w-0 flex-1">
              <span class="block truncate text-sm text-text">{tag.name}</span>
              <span class="mt-0.5 block truncate text-xs text-text-muted">
                {tag.type}
                {#if tag.paused}· {t("gtm.tags.paused")}{/if}
              </span>
            </div>
            {#if data.canWrite}
              <form method="POST" action="?/deleteTag" use:enhance={busy.clear(tag.tag_id)}>
                <input type="hidden" name="tag_id" value={tag.tag_id} />
                <Button type="submit" variant="secondary" size="xs" disabled={busy.active}>
                  {t("gtm.tags.delete")}
                </Button>
              </form>
            {/if}
          </li>
        {/each}
      </ul>
    {/if}
  </section>

  <!-- Triggers + variables -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="mb-3 text-sm font-semibold text-text">{t("gtm.triggers.title")}</h2>
    {#if data.triggers.length === 0}
      <p class="text-sm text-text-muted">{t("gtm.triggers.empty")}</p>
    {:else}
      <ul class="mb-5 divide-y divide-border">
        {#each data.triggers as trigger (trigger.trigger_id)}
          <li class="py-2">
            <span class="block truncate text-sm text-text">{trigger.name}</span>
            <span class="mt-0.5 block text-xs text-text-muted">{trigger.type}</span>
          </li>
        {/each}
      </ul>
    {/if}
    <h2 class="mb-3 mt-5 border-t border-border pt-4 text-sm font-semibold text-text">
      {t("gtm.variables.title")}
    </h2>
    {#if data.variables.length === 0}
      <p class="text-sm text-text-muted">{t("gtm.variables.empty")}</p>
    {:else}
      <ul class="divide-y divide-border">
        {#each data.variables as variable (variable.variable_id)}
          <li class="py-2">
            <span class="block truncate text-sm text-text">{variable.name}</span>
            <span class="mt-0.5 block text-xs text-text-muted">{variable.type}</span>
          </li>
        {/each}
      </ul>
    {/if}
  </section>
</div>

<!-- Versions: the last thing you do, and the only one with an audience -->
<section class="mt-6 rounded-xl border border-border bg-surface-raised p-5">
  <div class="mb-3 flex flex-wrap items-start justify-between gap-3">
    <h2 class="text-sm font-semibold text-text">{t("gtm.versions.title")}</h2>
    {#if data.canWrite}
      <!-- clear(): the name field starts something new each time. -->
      <form
        method="POST"
        action="?/version"
        use:enhance={busy.clear("version")}
        class="flex items-end gap-2"
      >
        <div>
          <label for="gtm-version-name" class="mb-1 block text-xs text-text-muted">
            {t("gtm.versions.create_name")}
          </label>
          <input id="gtm-version-name" name="name" class="{inputClass} sm:w-64" />
        </div>
        <Button type="submit" variant="secondary" size="sm" disabled={busy.active}>
          {t("gtm.versions.create")}
        </Button>
      </form>
    {/if}
  </div>

  {#if data.versions.length === 0}
    <p class="text-sm text-text-muted">{t("gtm.versions.empty")}</p>
  {:else}
    <ul class="divide-y divide-border">
      {#each data.versions as version (version.version_id)}
        <li class="flex items-start gap-3 py-2">
          <div class="min-w-0 flex-1">
            <span class="block truncate text-sm text-text">
              {version.version_id}
              {#if version.name}· {version.name}{/if}
              {#if version.live}
                <span class="ml-1 rounded bg-surface px-1.5 py-0.5 text-xs text-text-muted">
                  {t("gtm.versions.live")}
                </span>
              {/if}
            </span>
            <span class="mt-0.5 block text-xs text-text-muted">
              {t("gtm.versions.counts", {
                tags: version.num_tags,
                triggers: version.num_triggers,
                variables: version.num_variables,
              })}
            </span>
          </div>
          {#if data.canPublish && !version.live}
            <form method="POST" action="?/publish" use:enhance={busy.clear(version.version_id)}>
              <input type="hidden" name="version_id" value={version.version_id} />
              <Button
                type="submit"
                size="xs"
                disabled={busy.active}
                title={t("gtm.versions.publish_confirm", { version: version.version_id })}
              >
                {t("gtm.versions.publish")}
              </Button>
            </form>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
</section>

{#if data.canWrite}
  <Modal bind:open={adding} title={t("gtm.conversions.add")}>
    <!-- The mixed case, argued in place: it starts something new (`reset: true`), and a success
         also closes the dialog, which a bare `clear()` cannot express. -->
    <form
      method="POST"
      action="?/conversion"
      use:enhance={busy.wrap("conversion", () => async ({ update, result }) => {
        await update({ reset: true });
        if (result.type === "success") adding = false;
      })}
      class="space-y-4"
    >
      <div>
        <label for="gtm-conv-name" class="mb-1 block text-sm font-medium text-text">
          {t("gtm.conversions.name")}
        </label>
        <input id="gtm-conv-name" name="name" required class={inputClass} />
        <p class="mt-1 text-xs text-text-muted">{t("gtm.conversions.name_hint")}</p>
      </div>

      <div>
        <label for="gtm-conv-kind" class="mb-1 block text-sm font-medium text-text">
          {t("gtm.conversions.kind")}
        </label>
        <!-- A closed two-value vocabulary that decides which fields below apply: a radio-shaped
             choice, so the native control is right here and a type-ahead would be theatre. -->
        <select id="gtm-conv-kind" name="kind" bind:value={kind} class={selectClass}>
          <option value="ga4_event">{t("gtm.conversions.kind.ga4_event")}</option>
          <option value="ads_conversion">{t("gtm.conversions.kind.ads_conversion")}</option>
        </select>
      </div>

      {#if kind === "ga4_event"}
        <div class="grid gap-3 sm:grid-cols-2">
          <div>
            <label for="gtm-conv-event" class="mb-1 block text-sm font-medium text-text">
              {t("gtm.conversions.event_name")}
            </label>
            <input
              id="gtm-conv-event"
              name="event_name"
              placeholder="generate_lead"
              class={inputClass}
            />
            <p class="mt-1 text-xs text-text-muted">{t("gtm.conversions.event_name_hint")}</p>
          </div>
          <div>
            <label for="gtm-conv-measurement" class="mb-1 block text-sm font-medium text-text">
              {t("gtm.conversions.measurement_id")}
            </label>
            <input
              id="gtm-conv-measurement"
              name="measurement_id"
              placeholder="G-XXXXXXX"
              class={inputClass}
            />
            <p class="mt-1 text-xs text-text-muted">{t("gtm.conversions.measurement_id_hint")}</p>
          </div>
        </div>
      {:else}
        <div class="grid gap-3 sm:grid-cols-3">
          <div>
            <label for="gtm-conv-cid" class="mb-1 block text-sm font-medium text-text">
              {t("gtm.conversions.conversion_id")}
            </label>
            <input id="gtm-conv-cid" name="conversion_id" class={inputClass} />
          </div>
          <div>
            <label for="gtm-conv-label" class="mb-1 block text-sm font-medium text-text">
              {t("gtm.conversions.conversion_label")}
            </label>
            <input id="gtm-conv-label" name="conversion_label" class={inputClass} />
          </div>
          <div>
            <label for="gtm-conv-currency" class="mb-1 block text-sm font-medium text-text">
              {t("gtm.conversions.currency")}
            </label>
            <input
              id="gtm-conv-currency"
              name="currency_code"
              maxlength="3"
              placeholder="EUR"
              class={inputClass}
            />
          </div>
        </div>
      {/if}

      <div class="border-t border-border pt-4">
        <label for="gtm-conv-trigger" class="mb-1 block text-sm font-medium text-text">
          {t("gtm.conversions.trigger")}
        </label>
        <select
          id="gtm-conv-trigger"
          name="trigger_kind"
          bind:value={triggerKind}
          class={selectClass}
        >
          {#each TRIGGER_KINDS as option (option)}
            <option value={option}>{t(`gtm.conversions.trigger.${option}`)}</option>
          {/each}
        </select>
      </div>

      {#if triggerKind === "custom_event"}
        <div>
          <label for="gtm-conv-devent" class="mb-1 block text-sm font-medium text-text">
            {t("gtm.conversions.event_name")}
          </label>
          <input id="gtm-conv-devent" name="trigger_event_name" class={inputClass} />
        </div>
      {/if}
      {#if triggerKind === "element_click" || triggerKind === "element_visibility"}
        <div>
          <label for="gtm-conv-selector" class="mb-1 block text-sm font-medium text-text">
            {t("gtm.conversions.selector")}
          </label>
          <input id="gtm-conv-selector" name="selector" placeholder=".cta" class={inputClass} />
        </div>
      {/if}

      <div>
        <label for="gtm-conv-url" class="mb-1 block text-sm font-medium text-text">
          {t("gtm.conversions.url_contains")}
        </label>
        <input id="gtm-conv-url" name="url_contains" placeholder="/contact" class={inputClass} />
      </div>

      <div class="flex justify-end gap-2">
        <Button type="button" variant="secondary" onclick={() => (adding = false)}>
          {t("common.cancel")}
        </Button>
        <Button type="submit" disabled={busy.active}>{t("gtm.conversions.add")}</Button>
      </div>
    </form>
  </Modal>
{/if}
