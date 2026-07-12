<script lang="ts">
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";

  let { data, form } = $props();

  const ai = $derived(data.ai);
  const usage = $derived(data.usage);
  const FEATURES = ["assistant", "writing_assist", "time_assist", "reporting"] as const;

  const DEFAULT_MODELS: Record<string, string> = {
    anthropic: "claude-opus-4-8",
    openai: "gpt-5",
    openai_compatible: "",
  };

  let provider = $state(data.ai?.provider ?? "anthropic");
  let confirmRemove = $state(false);

  const budgetPct = $derived(
    usage?.budget ? Math.round((usage.tokens_total / usage.budget) * 100) : null,
  );

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";

  const fmt = new Intl.NumberFormat("nl-NL");
</script>

<svelte:head>
  <title>{pageTitle(t("settings.ai.title"))}</title>
</svelte:head>

<a href="/settings" class="text-sm text-text-muted hover:text-text">← {t("settings.title")}</a>
<h1 class="mb-1 mt-2 text-xl font-semibold text-text">{t("settings.ai.title")}</h1>
<p class="mb-6 text-sm text-text-muted">{t("settings.ai.subtitle")}</p>

<section class="max-w-2xl rounded-xl border border-border bg-surface-raised p-5">
  <!-- The privacy statement the tenant configures the provider under (#126): plain, on the
       page, not buried in docs. -->
  <p class="mb-5 rounded-lg bg-surface px-4 py-3 text-xs text-text-muted">
    {t("settings.ai.privacy_note")}
  </p>

  <form method="POST" action="?/save" use:enhance class="space-y-4">
    <div class="grid gap-4 sm:grid-cols-2">
      <div>
        <label for="ai-provider" class="mb-1 block text-sm text-text"
          >{t("settings.ai.provider")}</label
        >
        <select id="ai-provider" name="provider" class={inputClass} bind:value={provider}>
          <option value="anthropic">Anthropic</option>
          <option value="openai">OpenAI</option>
          <option value="openai_compatible">{t("settings.ai.provider_compatible")}</option>
        </select>
      </div>
      <div>
        <label for="ai-key" class="mb-1 block text-sm text-text">{t("settings.ai.api_key")}</label>
        <input
          id="ai-key"
          name="api_key"
          type="password"
          autocomplete="new-password"
          placeholder={ai?.has_key ? t("settings.ai.key_stored") : ""}
          class={inputClass}
        />
      </div>
      <div>
        <label for="ai-model" class="mb-1 block text-sm text-text"
          >{t("settings.ai.default_model")}</label
        >
        <input
          id="ai-model"
          name="default_model"
          autocomplete="off"
          value={ai?.default_model ?? ""}
          placeholder={DEFAULT_MODELS[provider]}
          class={inputClass}
        />
        <p class="mt-1 text-xs text-text-muted">{t("settings.ai.default_model_hint")}</p>
      </div>
      <div>
        <label for="ai-base-url" class="mb-1 block text-sm text-text"
          >{t("settings.ai.base_url")}</label
        >
        <input
          id="ai-base-url"
          name="base_url"
          type="url"
          placeholder="https://ai.example.com/v1"
          value={ai?.base_url ?? ""}
          class={inputClass}
        />
        <p class="mt-1 text-xs text-text-muted">{t("settings.ai.base_url_hint")}</p>
      </div>
    </div>

    <fieldset class="space-y-2 border-t border-border pt-4">
      <legend class="mb-1 text-sm font-medium text-text">{t("settings.ai.features")}</legend>
      <p class="text-xs text-text-muted">{t("settings.ai.features_hint")}</p>
      {#each FEATURES as feature (feature)}
        <div class="flex flex-wrap items-center gap-3">
          <label class="flex min-w-48 items-center gap-2 text-sm text-text">
            <input
              type="checkbox"
              name={`feature_${feature}`}
              checked={ai?.features?.[feature]?.enabled ?? true}
            />
            {t(`settings.ai.feature_${feature}`)}
          </label>
          <input
            name={`model_${feature}`}
            autocomplete="off"
            value={ai?.features?.[feature]?.model ?? ""}
            placeholder={t("settings.ai.feature_model_placeholder")}
            class="{inputClass} max-w-56 flex-1"
          />
        </div>
      {/each}
    </fieldset>

    <div class="border-t border-border pt-4">
      <label for="ai-house-style" class="mb-1 block text-sm text-text"
        >{t("settings.ai.house_style")}</label
      >
      <textarea
        id="ai-house-style"
        name="house_style"
        rows="3"
        class={inputClass}
        placeholder={t("settings.ai.house_style_placeholder")}>{ai?.house_style ?? ""}</textarea
      >
      <p class="mt-1 text-xs text-text-muted">{t("settings.ai.house_style_hint")}</p>
    </div>

    <div>
      <label for="ai-budget" class="mb-1 block text-sm text-text">{t("settings.ai.budget")}</label>
      <input
        id="ai-budget"
        name="monthly_token_budget"
        type="number"
        min="1"
        value={ai?.monthly_token_budget ?? ""}
        class="{inputClass} max-w-56"
      />
      <p class="mt-1 text-xs text-text-muted">{t("settings.ai.budget_hint")}</p>
    </div>

    {#if form?.error}<p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>{/if}
    {#if form?.fields}
      {#each Object.values(form.fields) as key (key)}
        <p class="text-sm text-red-600 dark:text-red-400">{t(String(key))}</p>
      {/each}
    {/if}
    {#if form?.saved}<p class="text-sm text-green-700 dark:text-green-400">
        {t("settings.ai.saved")}
      </p>{/if}

    <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
      >{t("common.save")}</button
    >
  </form>

  {#if ai}
    <form method="POST" action="?/test" use:enhance class="mt-4 border-t border-border pt-4">
      <button class="rounded-lg border border-border px-4 py-2 text-sm text-text hover:border-brand"
        >{t("settings.ai.test")}</button
      >
      {#if form?.test}
        {#if form.test.ok}
          <p class="mt-2 text-sm text-green-700 dark:text-green-400">
            {t("settings.ai.test_ok", { model: form.test.model ?? "?" })}
          </p>
        {:else}
          <p class="mt-2 text-sm text-red-600 dark:text-red-400">
            {t("settings.ai.test_failed", { error: form.test.error ?? "?" })}
          </p>
        {/if}
      {/if}
    </form>
  {/if}
</section>

{#if usage && (usage.features.length > 0 || usage.budget)}
  <section class="mt-6 max-w-2xl rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="mb-1 text-base font-semibold text-text">{t("settings.ai.usage_title")}</h2>
    <p class="mb-3 text-sm text-text-muted">
      {t("settings.ai.usage_month", { month: usage.month })}
    </p>
    {#if usage.budget}
      <div class="mb-3">
        <div class="mb-1 flex justify-between text-xs text-text-muted">
          <span
            >{t("settings.ai.usage_of_budget", {
              used: fmt.format(usage.tokens_total),
              budget: fmt.format(usage.budget),
            })}</span
          >
          <span>{budgetPct}%</span>
        </div>
        <div class="h-2 overflow-hidden rounded-full bg-surface">
          <div
            class="h-full rounded-full {budgetPct !== null && budgetPct >= 100
              ? 'bg-red-500'
              : budgetPct !== null && budgetPct >= 80
                ? 'bg-amber-500'
                : 'bg-brand'}"
            style={`width: ${Math.min(100, budgetPct ?? 0)}%`}
          ></div>
        </div>
        {#if budgetPct !== null && budgetPct >= 80 && budgetPct < 100}
          <p class="mt-1 text-xs text-amber-600 dark:text-amber-400">
            {t("settings.ai.budget_warning")}
          </p>
        {:else if budgetPct !== null && budgetPct >= 100}
          <p class="mt-1 text-xs text-red-600 dark:text-red-400">
            {t("settings.ai.budget_reached")}
          </p>
        {/if}
      </div>
    {/if}
    <ul class="space-y-1 text-sm text-text">
      {#each usage.features as row (row.feature)}
        {@const featureKey = `settings.ai.feature_${row.feature}`}
        <li class="flex justify-between">
          <span>{t(featureKey) === featureKey ? row.feature : t(featureKey)}</span>
          <span class="text-text-muted"
            >{t("settings.ai.usage_row", {
              requests: fmt.format(row.requests),
              tokens: fmt.format(row.tokens_in + row.tokens_out),
            })}</span
          >
        </li>
      {/each}
    </ul>
  </section>
{/if}

{#if ai}
  <section class="mt-6 max-w-2xl">
    <button
      type="button"
      class="text-sm text-red-600 hover:underline dark:text-red-400"
      onclick={() => (confirmRemove = true)}>{t("settings.ai.remove")}</button
    >
    <ConfirmDialog
      bind:open={confirmRemove}
      title={t("settings.ai.remove")}
      message={t("settings.ai.remove_confirm")}
      action="?/remove"
      confirmLabel={t("common.delete")}
    />
  </section>
{/if}
