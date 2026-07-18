<script lang="ts">
  import { Pencil, ScrollText, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";

  let { data, form } = $props();

  let deleteId = $state("");
  let confirmDelete = $state(false);

  // Inline enabled switch posts through one shared hidden form (custom-fields pattern).
  let toggleForm: HTMLFormElement | undefined = $state();
  let toggleId = $state("");
  let toggleValue = $state("true");
  function requestToggle(id: string, enabled: boolean) {
    toggleId = id;
    toggleValue = String(enabled);
    setTimeout(() => toggleForm?.requestSubmit(), 0);
  }
</script>

<svelte:head>
  <title>{pageTitle(t("automation.title"))}</title>
</svelte:head>

<div class="mb-6">
  <div class="mt-2 flex flex-wrap items-center gap-3">
    <h1 class="text-xl font-semibold text-text">{t("automation.title")}</h1>
    <div class="flex-1"></div>
    {#if data.canReadRuns}
      <a
        href="/settings/automation/runs"
        class="rounded-lg border border-border px-4 py-2 text-sm text-text hover:border-brand"
      >
        {t("automation.runs")}
      </a>
    {/if}
    {#if data.canWrite}
      <a
        href="/settings/automation/new"
        class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
      >
        {t("automation.new_rule")}
      </a>
    {/if}
  </div>
  <p class="mt-1 text-sm text-text-muted">{t("automation.subtitle")}</p>
</div>

<div class="rounded-xl border border-border bg-surface-raised">
  {#if data.rules.length === 0}
    <p class="p-6 text-center text-sm text-text-muted">{t("automation.rules_empty")}</p>
  {:else}
    <div class="overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr
            class="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted"
          >
            <th class="px-4 py-2 font-medium">{t("automation.name")}</th>
            <th class="px-4 py-2 font-medium">{t("automation.trigger")}</th>
            <th class="px-4 py-2 font-medium">{t("automation.actions")}</th>
            <th class="px-4 py-2 font-medium">{t("automation.enabled")}</th>
            <th class="px-4 py-2 text-right font-medium">{t("common.actions")}</th>
          </tr>
        </thead>
        <tbody>
          {#each data.rules as rule (rule.id)}
            <tr class="border-b border-border" class:opacity-50={!rule.enabled}>
              <td class="px-4 py-2">
                {#if data.canWrite}
                  <a href={`/settings/automation/${rule.id}`} class="text-text hover:text-brand">
                    {rule.name}
                  </a>
                {:else}
                  <span class="text-text">{rule.name}</span>
                {/if}
              </td>
              <td class="px-4 py-2 text-text-muted">
                {t(`automation.trigger.${rule.trigger_event}`)}
              </td>
              <td class="px-4 py-2 text-text-muted">
                {(rule.actions ?? [])
                  .map((a) => t(`automation.action.${a.action_type}`))
                  .join(", ") || "—"}
              </td>
              <td class="px-4 py-2">
                <input
                  type="checkbox"
                  checked={rule.enabled}
                  disabled={!data.canWrite}
                  aria-label={t("automation.enabled")}
                  class="h-4 w-4 rounded border-border"
                  onchange={() => requestToggle(rule.id, !rule.enabled)}
                />
              </td>
              <td class="px-4 py-2">
                <div class="flex items-center justify-end">
                  {#if data.canWrite}
                    <ActionsMenu
                      items={[
                        {
                          label: t("common.edit"),
                          icon: Pencil,
                          href: `/settings/automation/${rule.id}`,
                        },
                        {
                          label: t("automation.runs"),
                          icon: ScrollText,
                          href: `/settings/automation/runs?rule_id=${rule.id}`,
                        },
                        {
                          label: t("common.delete"),
                          icon: Trash2,
                          danger: true,
                          onclick: () => {
                            deleteId = rule.id;
                            confirmDelete = true;
                          },
                        },
                      ]}
                    />
                  {/if}
                </div>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>

{#if form?.error}<p class="mt-2 text-sm text-red-600">{t(form.error)}</p>{/if}

<form method="POST" action="?/toggle" use:enhance bind:this={toggleForm} class="hidden">
  <input type="hidden" name="id" value={toggleId} />
  <input type="hidden" name="enabled" value={toggleValue} />
</form>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("common.delete")}
  message={t("automation.delete_confirm")}
  action="?/delete"
  fields={{ id: deleteId }}
/>
