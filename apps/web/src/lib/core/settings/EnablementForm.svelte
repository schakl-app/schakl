<script lang="ts">
  /**
   * The form behind Instellingen → Modules and Instellingen → Integraties (issue #378).
   *
   * The two screens differ in exactly three things — which kind they list, whether the hub is on
   * them, and the words at the top — and agree about everything that is hard: the fixpoint that
   * turns "what the reader ticked" into "what can be saved", the hidden inputs that post the
   * **whole** `enabled_modules` list rather than this screen's half, and the sentence naming what
   * is about to be dropped. Two copies of that is how one screen starts wiping the other's half.
   *
   * **The casualty may be on the other screen, and that is the case worth stating.** Switching
   * Domeinen off here does not only drop Domeinen: Cloudflare and OXXA have nowhere to put their
   * data without it (`ModuleDescriptor.requires`), so they go too — and they are listed on a
   * screen the reader is not looking at. The warning therefore names *everything* dropped, marks
   * which of it is elsewhere, and links there. The alternative is a silent removal, or the API's
   * 409 quoting a rule nothing on the page had mentioned.
   */
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { moduleLabel } from "$lib/core/registry";
  import { effectiveModules, type EnablementData } from "$lib/core/settings/enablement";
  import ModuleToggleList from "$lib/core/settings/ModuleToggleList.svelte";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";

  let {
    data,
    form,
    kind,
    isInstanceOwner = false,
  }: {
    data: EnablementData;
    form: { error?: string; updated?: boolean } | null;
    kind: "module" | "integration";
    isInstanceOwner?: boolean;
  } = $props();

  const busy = new InFlight();

  const kindOf = (name: string) => (data.kinds[name] ?? "module") as "module" | "integration";

  /** This screen's own names, from the installation's list — not the tenant's. */
  const names = $derived(data.available.filter((name) => kindOf(name) === kind));

  // Component state via bind:group, never one-way checked={…} (docs/UX.md): a checkbox rendered
  // one-way loses its mark on hydration, and the next save then silently strips every module the
  // user never touched — only the freshly ticked ones survived.
  //
  // **Which is exactly why the state is split in two.** `bind:group` does not mean "add or remove
  // this value": it means "this array *is* what the rendered group has selected", and it rebuilds
  // the array from the checkboxes on the page. Binding the whole `enabled_modules` list to a group
  // that only renders *this* kind therefore deleted the other kind on the first click — unticking
  // Domeinen under Modules posted a set with Google, Cloudflare, OXXA and Mollie silently gone,
  // and no warning could fire because they had left `ticked` rather than been dropped from it.
  // Caught in a browser, invisible in review, and a data loss rather than a display fault.
  //
  // So `here` is the group's own selection and `others` is the half this screen does not render,
  // carried through untouched — except when something ticked here *requires* something over
  // there, which is the one way `others` grows.
  let here = $state<string[]>(data.enabled.filter((name) => kindOf(name) === kind));
  let others = $state<string[]>(data.enabled.filter((name) => kindOf(name) !== kind));

  // What is ticked and what will be saved are two different lists, which is the whole trick here.
  // `ticked` is what the reader asked for; `effective` is the nearest *valid* set to it. Deriving
  // the second from the first rather than correcting `ticked` in an effect is what makes the
  // warning self-clearing: re-tick Domeinen and the Cloudflare line goes away by recomputation,
  // with nothing to remember to reset.
  const ticked = $derived([...others, ...here]);

  const effective = $derived(effectiveModules(ticked, data.requires));

  /** Ticked, but not saveable — the reader asked for it and something it needs is switched off. */
  const dropped = $derived(ticked.filter((name) => !effective.includes(name)));

  /**
   * Ticking something on switches on what it needs, transitively — into whichever half holds it.
   *
   * The other direction is left to `effective`: silently re-enabling a module somebody just
   * switched off would be the screen arguing with them, while adding what they clearly meant to
   * add finishes the sentence they started. A fixpoint by queue, because a requirement may itself
   * require something (`google_ads` → `google`).
   */
  function pullInRequirements(name: string): void {
    const queue = [...(data.requires[name] ?? [])];
    while (queue.length) {
      const need = queue.shift()!;
      if (here.includes(need) || others.includes(need)) continue;
      if (kindOf(need) === kind) here = [...here, need];
      else others = [...others, need];
      queue.push(...(data.requires[need] ?? []));
    }
  }

  /** Dropped rows the reader can see here, and the ones they cannot. */
  const droppedHere = $derived(dropped.filter((name) => kindOf(name) === kind));
  const droppedElsewhere = $derived(dropped.filter((name) => kindOf(name) !== kind));

  const otherHref = $derived(kind === "module" ? "/settings/integrations" : "/settings/modules");
  const otherKey = $derived(
    kind === "module"
      ? "settings.modules.also_drops_integrations"
      : "settings.modules.also_drops_modules",
  );

  const names_ = (keys: string[]) => keys.map(moduleLabel).join(", ");
</script>

<form
  method="POST"
  action="?/update"
  use:enhance={busy.wrap("", () => async ({ update }) => {
    // Keep the ticked state after save (docs/UX.md): the default reset would wipe the checkboxes
    // back to their SSR attributes.
    await update({ reset: false });
  })}
  class="max-w-2xl space-y-6"
>
  <!-- What actually gets saved: the valid set over **both** kinds, not this screen's boxes.
       `companies` is always in it — it is the hub, its checkbox is disabled, and a disabled
       checkbox posts nothing. -->
  {#each effective as name (name)}
    <input type="hidden" name="modules" value={name} />
  {/each}

  <ModuleToggleList
    {names}
    bind:here
    {ticked}
    onenable={pullInRequirements}
    {kind}
    hub={kind === "module" ? "companies" : null}
    licensed={data.licensed}
    entitled={data.entitled}
    enabled={data.enabled}
    requires={data.requires}
    deployment={data.deployment}
    {isInstanceOwner}
    {dropped}
  />

  <div>
    <p class="text-xs text-text-muted">{t("settings.modules.hint")}</p>
    {#if dropped.length}
      <!-- Not an error and not a block: the save goes through, and this says what will not be in
           it. The reader fixes it by ticking what the row names, at which point this line
           disappears by recomputation. -->
      <div class="mt-2 space-y-1 text-sm text-amber-700 dark:text-amber-400">
        {#if droppedHere.length}
          <p>{t("settings.modules.dropped")}: {names_(droppedHere)}</p>
        {/if}
        {#if droppedElsewhere.length}
          <p>
            {t(otherKey)}: {names_(droppedElsewhere)}
            <a class="underline hover:no-underline" href={otherHref}>
              {t(kind === "module" ? "settings.integrations.title" : "settings.modules.title")}</a
            >
          </p>
        {/if}
      </div>
    {/if}
    {#if form?.error}<p class="mt-2 text-sm text-red-600">{t(form.error)}</p>{/if}
    {#if form?.updated}
      <p class="mt-2 text-sm text-green-600">{t("settings.account.saved")}</p>
    {/if}
    <Button class="mt-4" loading={busy.active}>
      {t("common.save")}
    </Button>
  </div>
</form>
