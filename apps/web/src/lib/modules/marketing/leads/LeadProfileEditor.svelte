<script lang="ts">
  /**
   * The measurement profile editor (docs/MARKETING.md): one client's vocabulary for the leads
   * dashboard, picked from what their GA4 property and Ads account actually carry.
   *
   * The whole profile is one record, edited in state and posted as one JSON value — a form of
   * two hundred named inputs would be a second schema to keep in step with the API's, which
   * validates the record whole and names the field it refuses. Nothing here is a default for
   * any client: an empty profile draws nothing, and every event name is typed or picked.
   */
  import { Plus, X } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { localeLabel, t } from "$lib/core/i18n";
  import { editLocales } from "$lib/core/i18n-edit.svelte";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";

  import {
    DIMENSION_KEYS,
    MATCH_KINDS,
    ROLES,
    STANDARD_DIMENSIONS,
    WIDGET_KEYS,
    emptyProfile,
    type DimensionKey,
    type DimensionSpec,
    type LeadProfile,
    type LeadsCatalog,
    type Role,
  } from "./types";

  let {
    companyId,
    profile,
    links,
    catalog,
    catalogPending = false,
    saved = false,
    error = null,
    errorDetail = null,
  }: {
    companyId: string;
    profile: LeadProfile | null;
    links: { id: string; source: string; display_name: string }[];
    catalog: LeadsCatalog | null;
    catalogPending?: boolean;
    saved?: boolean;
    error?: string | null;
    errorDetail?: string | null;
  } = $props();

  const busy = new InFlight();
  const locales = editLocales();
  const inputClass =
    "w-full rounded-lg border border-border bg-surface px-2.5 py-1.5 text-sm text-text outline-none focus:border-brand";
  const smallInput =
    "rounded border border-border bg-surface px-2 py-1 text-sm text-text outline-none focus:border-brand";

  // A deep copy, seeded once: a save that is refused leaves the typed draft in place, and a
  // refreshed prop must not yank the fields around under the person typing into them.
  // svelte-ignore state_referenced_locally
  let draft = $state<LeadProfile>(
    profile ? (JSON.parse(JSON.stringify(profile)) as LeadProfile) : emptyProfile(),
  );
  let removing = $state(false);

  // Value labels are edited as lines of `raw = label` per locale; the map is rebuilt on save.
  const valueLines = $state<Record<string, Record<string, string>>>({});
  for (const key of DIMENSION_KEYS) {
    const spec = draft.dimensions[key];
    valueLines[key] = {};
    for (const locale of locales) {
      valueLines[key][locale] = Object.entries(spec?.values ?? {})
        .map(([raw, labels]) => (labels[locale] ? `${raw} = ${labels[locale]}` : null))
        .filter(Boolean)
        .join("\n");
    }
  }
  let quoteValues = $state(draft.quote_values.join(", "));

  const ga4Links = $derived(links.filter((l) => l.source === "ga4"));
  const gadsLinks = $derived(links.filter((l) => l.source === "gads"));
  const eventNames = $derived((catalog?.events ?? []).map((e) => e.name));
  const dimensionFields = $derived([
    ...(catalog?.custom_dimensions ?? []).map((d) => d.field),
    ...STANDARD_DIMENSIONS,
  ]);
  const serviceValues = $derived(Object.keys(draft.dimensions.service?.values ?? {}));
  const actionNames = $derived(
    Array.from(
      new Set([
        ...(catalog?.conversion_actions ?? []).map((a) => a.name),
        ...Object.keys(draft.ads.action_services),
      ]),
    ),
  );

  function addMatcher(role: Role) {
    draft.roles[role] = [...(draft.roles[role] ?? []), { match: "exact", value: "" }];
  }
  function removeMatcher(role: Role, index: number) {
    draft.roles[role] = (draft.roles[role] ?? []).filter((_, i) => i !== index);
  }
  function toggleDimension(key: DimensionKey, on: boolean) {
    if (on) {
      draft.dimensions[key] = draft.dimensions[key] ?? {
        field: `customEvent:${key === "service" ? "dienst" : key}`,
        label: {},
        values: {},
        filterable: true,
      };
    } else {
      delete draft.dimensions[key];
    }
  }
  function addBreakpoint() {
    draft.breakpoints = [...draft.breakpoints, { date: "", description: {}, severity: "hard" }];
  }
  function removeBreakpoint(index: number) {
    draft.breakpoints = draft.breakpoints.filter((_, i) => i !== index);
  }
  function widgetHidden(key: string): boolean {
    return draft.hidden_widgets.includes(key);
  }
  function toggleWidget(key: string, shown: boolean) {
    draft.hidden_widgets = shown
      ? draft.hidden_widgets.filter((k) => k !== key)
      : [...draft.hidden_widgets, key];
  }

  function parseValueLines(key: DimensionKey): DimensionSpec["values"] {
    const out: DimensionSpec["values"] = {};
    for (const locale of locales) {
      for (const line of (valueLines[key]?.[locale] ?? "").split("\n")) {
        const i = line.indexOf("=");
        if (i <= 0) continue;
        const raw = line.slice(0, i).trim();
        const label = line.slice(i + 1).trim();
        if (!raw || !label) continue;
        out[raw] = { ...(out[raw] ?? {}), [locale]: label };
      }
    }
    return out;
  }

  /** The record as the API validates it: empty matchers dropped, value maps rebuilt. */
  const serialized = $derived.by(() => {
    const roles: LeadProfile["roles"] = {};
    for (const role of ROLES) {
      const matchers = (draft.roles[role] ?? []).filter((m) => m.value.trim());
      if (matchers.length)
        roles[role] = matchers.map((m) => ({ match: m.match, value: m.value.trim() }));
    }
    const dimensions: LeadProfile["dimensions"] = {};
    for (const key of DIMENSION_KEYS) {
      const spec = draft.dimensions[key];
      if (!spec || !spec.field.trim()) continue;
      const label: Record<string, string> = {};
      for (const [locale, text] of Object.entries(spec.label))
        if (text?.trim()) label[locale] = text.trim();
      dimensions[key] = {
        field: spec.field.trim(),
        label,
        values: parseValueLines(key),
        filterable: spec.filterable,
      };
    }
    const action_services: Record<string, string> = {};
    for (const [action, service] of Object.entries(draft.ads.action_services)) {
      if (action.trim() && service.trim()) action_services[action.trim()] = service.trim();
    }
    const disclaimer: Record<string, string> = {};
    for (const [locale, text] of Object.entries(draft.disclaimer))
      if (text?.trim()) disclaimer[locale] = text.trim();
    const record: LeadProfile = {
      ga4_link_id: draft.ga4_link_id || null,
      gads_link_id: draft.gads_link_id || null,
      roles,
      dimensions,
      quote_values: quoteValues
        .split(",")
        .map((v) => v.trim())
        .filter(Boolean),
      ads: { enabled: draft.ads.enabled, action_services },
      breakpoints: draft.breakpoints
        .filter((b) => b.date)
        .map((b) => ({
          date: b.date,
          severity: b.severity,
          description: Object.fromEntries(
            Object.entries(b.description).filter(([, v]) => v?.trim()),
          ),
        })),
      channel_groups: null,
      hidden_widgets: draft.hidden_widgets,
      disclaimer,
    };
    return JSON.stringify(record);
  });
</script>

<form method="POST" action="?/save" use:enhance={busy.keep()} class="max-w-3xl space-y-6">
  <input type="hidden" name="company_id" value={companyId} />
  <input type="hidden" name="lead_profile" value={serialized} />

  {#if catalogPending}
    <p class="text-xs text-text-muted">{t("marketing.leads.profile.catalog_loading")}</p>
  {:else if catalog && !catalog.ga4_available}
    <p
      class="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-100"
    >
      {t("marketing.leads.profile.catalog_unavailable")}
      {#if catalog.unavailable_reason}({t(catalog.unavailable_reason)}){/if}
    </p>
  {/if}

  <datalist id="lead-events">
    {#each eventNames as name (name)}<option value={name}></option>{/each}
  </datalist>
  <datalist id="lead-dimension-fields">
    {#each dimensionFields as field (field)}<option value={field}></option>{/each}
  </datalist>
  <datalist id="lead-service-values">
    {#each serviceValues as value (value)}<option {value}></option>{/each}
  </datalist>

  <!-- Sources -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="mb-3 text-sm font-semibold text-text">{t("marketing.leads.profile.sources")}</h2>
    <div class="grid gap-3 sm:grid-cols-2">
      <div>
        <label for="lead-ga4-link" class="mb-1 block text-xs text-text-muted"
          >{t("marketing.leads.profile.ga4_link")}</label
        >
        <select id="lead-ga4-link" bind:value={draft.ga4_link_id} class={inputClass}>
          <option value={null}>{t("marketing.leads.profile.first_link")}</option>
          {#each ga4Links as link (link.id)}<option value={link.id}>{link.display_name}</option
            >{/each}
        </select>
      </div>
      <div>
        <label for="lead-gads-link" class="mb-1 block text-xs text-text-muted"
          >{t("marketing.leads.profile.gads_link")}</label
        >
        <select id="lead-gads-link" bind:value={draft.gads_link_id} class={inputClass}>
          <option value={null}>{t("marketing.leads.profile.first_link")}</option>
          {#each gadsLinks as link (link.id)}<option value={link.id}>{link.display_name}</option
            >{/each}
        </select>
        <label class="mt-2 flex items-center gap-2 text-sm text-text">
          <input type="checkbox" bind:checked={draft.ads.enabled} class="rounded border-border" />
          {t("marketing.leads.profile.ads_enabled")}
        </label>
      </div>
    </div>
  </section>

  <!-- Roles -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="mb-1 text-sm font-semibold text-text">{t("marketing.leads.profile.roles")}</h2>
    <p class="mb-3 text-xs text-text-muted">{t("marketing.leads.profile.roles_hint")}</p>
    <div class="space-y-4">
      {#each ROLES as role (role)}
        <div>
          <div class="flex items-baseline justify-between gap-2">
            <p class="text-sm font-medium text-text">{t(`marketing.leads.role.${role}`)}</p>
            <button
              type="button"
              class="flex items-center gap-1 text-xs text-brand hover:underline"
              onclick={() => addMatcher(role)}
            >
              <Plus size={12} />
              {t("marketing.leads.profile.add_matcher")}
            </button>
          </div>
          <p class="mb-1.5 text-xs text-text-muted">{t(`marketing.leads.role_help.${role}`)}</p>
          {#each draft.roles[role] ?? [] as matcher, i (i)}
            <div class="mb-1.5 flex items-center gap-1.5">
              <select
                bind:value={matcher.match}
                class={smallInput}
                aria-label={t("marketing.leads.profile.match_kind")}
              >
                {#each MATCH_KINDS as kind (kind)}
                  <option value={kind}>{t(`marketing.leads.profile.match.${kind}`)}</option>
                {/each}
              </select>
              <input
                bind:value={matcher.value}
                list="lead-events"
                placeholder={t("marketing.leads.profile.event_name")}
                maxlength="100"
                class="{smallInput} min-w-0 flex-1"
              />
              <button
                type="button"
                class="rounded p-1 text-text-muted hover:text-red-600 dark:hover:text-red-400"
                aria-label={t("common.remove")}
                onclick={() => removeMatcher(role, i)}
              >
                <X size={14} />
              </button>
            </div>
          {/each}
        </div>
      {/each}
    </div>
  </section>

  <!-- Dimensions -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="mb-1 text-sm font-semibold text-text">{t("marketing.leads.profile.dimensions")}</h2>
    <p class="mb-3 text-xs text-text-muted">{t("marketing.leads.profile.dimensions_hint")}</p>
    <div class="space-y-4">
      {#each DIMENSION_KEYS as key (key)}
        {@const spec = draft.dimensions[key]}
        <div class="rounded-lg border border-border p-3">
          <label class="flex items-center gap-2 text-sm font-medium text-text">
            <input
              type="checkbox"
              checked={Boolean(spec)}
              onchange={(e) => toggleDimension(key, e.currentTarget.checked)}
              class="rounded border-border"
            />
            {t(`marketing.leads.dimension.${key}`)}
          </label>
          {#if spec}
            <div class="mt-2 grid gap-2 sm:grid-cols-2">
              <div>
                <label for={`lead-dim-field-${key}`} class="mb-1 block text-xs text-text-muted">
                  {t("marketing.leads.profile.dimension_field")}
                </label>
                <input
                  id={`lead-dim-field-${key}`}
                  bind:value={spec.field}
                  list="lead-dimension-fields"
                  class={inputClass}
                />
              </div>
              {#each locales as locale (locale)}
                <div>
                  <label
                    for={`lead-dim-label-${key}-${locale}`}
                    class="mb-1 block text-xs text-text-muted"
                  >
                    {t("marketing.leads.profile.dimension_label", {
                      language: localeLabel(locale),
                    })}
                  </label>
                  <input
                    id={`lead-dim-label-${key}-${locale}`}
                    value={spec.label[locale] ?? ""}
                    oninput={(e) => (spec.label[locale] = e.currentTarget.value)}
                    maxlength="80"
                    class={inputClass}
                  />
                </div>
              {/each}
            </div>
            <label class="mt-2 flex items-center gap-2 text-xs text-text">
              <input type="checkbox" bind:checked={spec.filterable} class="rounded border-border" />
              {t("marketing.leads.profile.filterable")}
            </label>
            <div class="mt-2 grid gap-2 sm:grid-cols-2">
              {#each locales as locale (locale)}
                <div>
                  <label
                    for={`lead-dim-values-${key}-${locale}`}
                    class="mb-1 block text-xs text-text-muted"
                  >
                    {t("marketing.leads.profile.dimension_values", {
                      language: localeLabel(locale),
                    })}
                  </label>
                  <textarea
                    id={`lead-dim-values-${key}-${locale}`}
                    bind:value={valueLines[key][locale]}
                    rows="3"
                    placeholder={t("marketing.leads.profile.dimension_values_hint")}
                    class={inputClass}></textarea>
                </div>
              {/each}
            </div>
          {/if}
        </div>
      {/each}
    </div>
    <div class="mt-4">
      <label for="lead-quote-values" class="mb-1 block text-sm font-medium text-text">
        {t("marketing.leads.profile.quote_values")}
      </label>
      <input
        id="lead-quote-values"
        bind:value={quoteValues}
        class={inputClass}
        placeholder="offerte"
      />
      <p class="mt-1 text-xs text-text-muted">{t("marketing.leads.profile.quote_values_hint")}</p>
    </div>
  </section>

  <!-- Ads: conversion action → service -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="mb-1 text-sm font-semibold text-text">{t("marketing.leads.profile.actions")}</h2>
    <p class="mb-3 text-xs text-text-muted">{t("marketing.leads.profile.actions_hint")}</p>
    {#if actionNames.length === 0}
      <p class="text-sm text-text-muted">{t("marketing.leads.profile.no_actions")}</p>
    {:else}
      <div class="space-y-1.5">
        {#each actionNames as action (action)}
          <div class="flex items-center gap-2">
            <span class="w-64 shrink-0 truncate text-sm text-text" title={action}>{action}</span>
            <input
              value={draft.ads.action_services[action] ?? ""}
              oninput={(e) => (draft.ads.action_services[action] = e.currentTarget.value)}
              list="lead-service-values"
              placeholder={t("marketing.leads.profile.action_service")}
              class="{smallInput} min-w-0 flex-1"
            />
          </div>
        {/each}
      </div>
    {/if}
  </section>

  <!-- Breakpoints -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <div class="mb-1 flex items-baseline justify-between gap-2">
      <h2 class="text-sm font-semibold text-text">{t("marketing.leads.profile.breakpoints")}</h2>
      <button
        type="button"
        class="flex items-center gap-1 text-xs text-brand hover:underline"
        onclick={addBreakpoint}
      >
        <Plus size={12} />
        {t("marketing.leads.profile.add_breakpoint")}
      </button>
    </div>
    <p class="mb-3 text-xs text-text-muted">{t("marketing.leads.profile.breakpoints_hint")}</p>
    {#each draft.breakpoints as bp, i (i)}
      <div
        class="mb-2 grid items-end gap-2 rounded-lg border border-border p-3 sm:grid-cols-[10rem_8rem_1fr_auto]"
      >
        <DateInput name={`breakpoint-${i}`} bind:value={bp.date} />
        <select
          bind:value={bp.severity}
          class={inputClass}
          aria-label={t("marketing.leads.profile.severity")}
        >
          <option value="hard">{t("marketing.leads.profile.severity.hard")}</option>
          <option value="soft">{t("marketing.leads.profile.severity.soft")}</option>
        </select>
        <div class="grid gap-1">
          {#each locales as locale (locale)}
            <input
              value={bp.description[locale] ?? ""}
              oninput={(e) => (bp.description[locale] = e.currentTarget.value)}
              placeholder={t("marketing.leads.profile.breakpoint_text", {
                language: localeLabel(locale),
              })}
              maxlength="200"
              class={inputClass}
            />
          {/each}
        </div>
        <button
          type="button"
          class="rounded p-1 text-text-muted hover:text-red-600 dark:hover:text-red-400"
          aria-label={t("common.remove")}
          onclick={() => removeBreakpoint(i)}
        >
          <X size={14} />
        </button>
      </div>
    {/each}
  </section>

  <!-- Widgets -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="mb-1 text-sm font-semibold text-text">{t("marketing.leads.profile.widgets")}</h2>
    <p class="mb-3 text-xs text-text-muted">{t("marketing.leads.profile.widgets_hint")}</p>
    <div class="grid gap-1.5 sm:grid-cols-2">
      {#each WIDGET_KEYS as key (key)}
        <label class="flex items-center gap-2 text-sm text-text">
          <input
            type="checkbox"
            checked={!widgetHidden(key)}
            onchange={(e) => toggleWidget(key, e.currentTarget.checked)}
            class="rounded border-border"
          />
          {t(`marketing.leads.widget.${key}`)}
        </label>
      {/each}
    </div>
  </section>

  <!-- Disclaimer -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="mb-1 text-sm font-semibold text-text">{t("marketing.leads.profile.disclaimer")}</h2>
    <p class="mb-3 text-xs text-text-muted">{t("marketing.leads.profile.disclaimer_hint")}</p>
    <div class="grid gap-2 sm:grid-cols-2">
      {#each locales as locale (locale)}
        <textarea
          value={draft.disclaimer[locale] ?? ""}
          oninput={(e) => (draft.disclaimer[locale] = e.currentTarget.value)}
          rows="3"
          maxlength="1000"
          aria-label={localeLabel(locale)}
          placeholder={localeLabel(locale)}
          class={inputClass}></textarea>
      {/each}
    </div>
  </section>

  {#if saved}
    <p class="text-sm text-green-600 dark:text-green-400">{t("marketing.leads.profile.saved")}</p>
  {:else if error}
    <p class="text-sm text-red-600 dark:text-red-400">
      {t(error)}
      {#if errorDetail}<span class="text-text-muted">({errorDetail})</span>{/if}
    </p>
  {/if}

  <div class="flex items-center justify-between gap-2">
    <Button type="submit" loading={busy.active}>{t("common.save")}</Button>
    {#if profile}
      <Button type="button" variant="danger-outline" onclick={() => (removing = true)}>
        {t("marketing.leads.profile.remove")}
      </Button>
    {/if}
  </div>
</form>

{#if profile}
  <ConfirmDialog
    bind:open={removing}
    title={t("marketing.leads.profile.remove")}
    message={t("marketing.leads.profile.remove_confirm")}
    confirmLabel={t("marketing.leads.profile.remove")}
    action="?/remove"
  />
{/if}
