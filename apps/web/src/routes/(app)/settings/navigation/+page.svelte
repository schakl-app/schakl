<script lang="ts">
  /** Instellingen → Navigatie (#169): the org-default sidebar order/visibility everyone
   *  without a personal layout inherits — Settings → Dashboard's sibling. */
  import "$lib/modules"; // populate the nav registry

  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { enabledWebModules } from "$lib/core/registry";
  import { pageTitle } from "$lib/core/title";
  import NavPrefEditor from "$lib/core/ui/NavPrefEditor.svelte";

  let { data, form } = $props();

  // Every module nav item the org has enabled, in declared order — not filtered by the
  // admin's own permissions: the default is for everyone.
  const navItems = $derived(
    enabledWebModules(page.data.theme?.enabledModules ?? [])
      .flatMap((m) => m.nav ?? [])
      .sort((a, b) => (a.position ?? 100) - (b.position ?? 100)),
  );
  const candidates = $derived(navItems.map((item) => ({ key: item.key, label: item.label() })));
  // The distinct groups those items declare (today: "assets"), with their declared heading as
  // the placeholder — so an admin can rename a group the same way as an item (#169).
  const groups = $derived.by(() => {
    const seen = new Set<string>();
    const out: { key: string; label: string }[] = [];
    for (const item of navItems) {
      if (item.group && !seen.has(item.group)) {
        seen.add(item.group);
        out.push({ key: item.group, label: t(`nav.group.${item.group}`) });
      }
    }
    return out;
  });
</script>

<svelte:head>
  <title>{pageTitle(t("settings.navigation.title"))}</title>
</svelte:head>

<div class="mb-6">
  <h1 class="mt-2 text-xl font-semibold text-text">{t("settings.navigation.title")}</h1>
  <p class="mt-1 text-sm text-text-muted">{t("settings.navigation.subtitle")}</p>
</div>

{#if form?.saved}
  <p class="mb-4 text-sm text-green-700 dark:text-green-400">{t("settings.account.saved")}</p>
{:else if form?.error}
  <p class="mb-4 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
{/if}

{#key data.defaultItems}
  <NavPrefEditor
    {candidates}
    initial={data.defaultItems}
    {groups}
    initialGroups={data.defaultGroups}
    renamable
    action="?/saveDefault"
  />
{/key}
