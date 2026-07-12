<script lang="ts">
  import { t } from "$lib/core/i18n";
  import RuleForm from "$lib/modules/automation/RuleForm.svelte";

  let { data, form } = $props();
</script>

<svelte:head>
  <title>{t("automation.new_rule")}</title>
</svelte:head>

<div class="mb-6">
  <a href="/settings/automation" class="text-sm text-text-muted hover:text-text"
    >← {t("automation.title")}</a
  >
  <h1 class="mt-2 text-xl font-semibold text-text">{t("automation.new_rule")}</h1>
</div>

<RuleForm
  triggers={data.catalog.triggers}
  actionTypes={data.catalog.actions}
  members={data.members}
  templates={data.templates}
  action="?/create"
  error={form?.error ?? null}
  dryRun={form?.dryRun ?? null}
/>
