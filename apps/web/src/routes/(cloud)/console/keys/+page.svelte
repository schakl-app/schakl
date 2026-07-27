<script lang="ts">
  import { enhance } from "$app/forms";
  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";

  let { data, form } = $props();

  const busy = new InFlight();

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<svelte:head>
  <title>{t("cloud.keys.title")}</title>
</svelte:head>

<div>
  <h1 class="text-xl font-semibold text-text">{t("cloud.keys.title")}</h1>
  <p class="mt-1 text-sm text-text-muted">{t("cloud.keys.subtitle")}</p>
</div>

{#if form?.secret}
  <div
    class="mt-4 rounded-xl border border-emerald-300 bg-emerald-50 p-4 dark:border-emerald-900 dark:bg-emerald-950/40"
  >
    <p class="text-sm font-medium text-emerald-800 dark:text-emerald-300">
      {t("cloud.keys.secret_hint")}
    </p>
    <code class="mt-2 block break-all font-mono text-sm text-text">{form.secret}</code>
  </div>
{/if}

<form
  method="POST"
  action="?/create"
  use:enhance={busy.wrap("create")}
  class="mt-6 flex max-w-md gap-2"
>
  <input
    name="name"
    required
    maxlength="255"
    placeholder={t("cloud.keys.name")}
    class={inputClass}
  />
  <Button class="whitespace-nowrap" loading={busy.is("create")} disabled={busy.active}>
    {t("cloud.keys.create")}
  </Button>
</form>
{#if form?.error}
  <p class="mt-2 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
{/if}

<div class="mt-6 overflow-x-auto rounded-xl border border-border bg-surface-raised">
  {#if data.keys.length === 0}
    <p class="px-4 py-6 text-sm text-text-muted">{t("cloud.keys.empty")}</p>
  {:else}
    <table class="w-full text-sm">
      <thead>
        <tr
          class="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted"
        >
          <th class="px-4 py-3">{t("cloud.keys.name")}</th>
          <th class="px-4 py-3">{t("cloud.keys.key")}</th>
          <th class="px-4 py-3">{t("cloud.keys.created")}</th>
          <th class="px-4 py-3">{t("cloud.keys.last_used")}</th>
          <th class="px-4 py-3"></th>
        </tr>
      </thead>
      <tbody>
        {#each data.keys as key (key.id)}
          <tr class="border-b border-border last:border-0" class:opacity-50={key.revoked_at}>
            <td class="px-4 py-3 font-medium text-text">{key.name}</td>
            <td class="px-4 py-3 font-mono text-xs text-text-muted">{key.key_redacted}</td>
            <td class="px-4 py-3 text-text-muted">{fmtDateTime(key.created_at)}</td>
            <td class="px-4 py-3 text-text-muted">
              {key.last_used_at ? fmtDateTime(key.last_used_at) : "—"}
            </td>
            <td class="px-4 py-3 text-right">
              {#if key.revoked_at}
                <span class="text-xs text-text-muted">{t("cloud.keys.revoked")}</span>
              {:else}
                <form
                  method="POST"
                  action="?/revoke"
                  use:enhance={busy.wrap(key.id)}
                  class="inline"
                >
                  <input type="hidden" name="key_id" value={key.id} />
                  <Button
                    variant="danger-outline"
                    size="sm"
                    loading={busy.is(key.id)}
                    disabled={busy.active}
                  >
                    {t("cloud.keys.revoke")}
                  </Button>
                </form>
              {/if}
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
</div>
