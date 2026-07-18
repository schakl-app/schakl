<script lang="ts">
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import RuleForm from "$lib/modules/automation/RuleForm.svelte";

  let { data, form } = $props();
</script>

<svelte:head>
  <title>{pageTitle(t("automation.edit_rule"))}</title>
</svelte:head>

<div class="mb-6">
  <h1 class="mt-2 text-xl font-semibold text-text">{t("automation.edit_rule")}</h1>
</div>

{#key data.rule.id}
  <RuleForm
    rule={{ ...data.rule, actions: data.rule.actions ?? [] }}
    triggers={data.catalog.triggers}
    actionTypes={data.catalog.actions}
    members={data.members}
    templates={data.templates}
    action="?/update"
    error={form?.error ?? null}
    dryRun={form?.dryRun ?? null}
  />
{/key}
