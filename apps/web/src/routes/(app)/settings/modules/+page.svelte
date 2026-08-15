<script lang="ts">
  /**
   * Instellingen → Modules: what this workspace runs, in two lists (CLAUDE.md §6a).
   *
   * One list was one list too few. A module is a capability we provide and an integration is a
   * conversation with somebody else's service, and the difference shows up in every question a
   * reader has here: what does this cost, what happens if I switch it off, why is this one
   * greyed out. Cloudflare greyed out because `domains` is off is a completely different fact
   * from Facturatie greyed out because the licence does not cover it, and a single alphabetical
   * column of checkboxes said neither.
   *
   * The requirement is *derived*, not validated. Ticking a module off that an integration needs
   * unticks that integration in front of the reader and says so, rather than letting them press
   * save and meet a 409 naming a rule nothing on the page had mentioned. The API refuses the
   * invalid set anyway (`ensure_requirements_met`) — that is the backstop for the callers that
   * are not this screen, and a backstop should never be the thing a user meets first.
   */
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { moduleLabel } from "$lib/core/registry";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import Button from "$lib/core/ui/Button.svelte";

  let { data, form } = $props();

  const busy = new InFlight();

  // Component state via bind:group, never one-way checked={…} (docs/UX.md): a checkbox
  // rendered one-way loses its mark on hydration, and the next save then silently strips
  // every module the user never touched — only the freshly ticked ones survived.
  //
  // What is ticked and what will be saved are two different lists, which is the whole trick on
  // this screen. `ticked` is what the reader asked for; `effective` is the nearest *valid* set to
  // it. Deriving the second from the first rather than correcting `ticked` in an effect is what
  // makes the warning self-clearing: re-tick Domeinen and the Cloudflare line goes away by
  // recomputation, with nothing to remember to reset.
  let ticked = $state<string[]>([...data.enabled]);

  // A locked module means two different things, and only one of them is something the reader
  // can act on. On a self-hosted box a licence key is missing and the instance owner installs
  // one; on cloud the workspace's plan does not cover it and only the operator can change that,
  // so pointing at Instellingen → Licentie would be a control that always refuses (#253).
  const lockedHint = $derived(
    t(
      data.deployment === "cloud"
        ? "settings.modules.locked_hint_cloud"
        : "settings.modules.locked_hint",
    ),
  );

  const requires = (name: string): string[] => data.requires[name] ?? [];

  /**
   * The valid set nearest to what is ticked: everything whose requirements are themselves in it.
   *
   * A fixpoint rather than one pass because a requirement may itself be an integration —
   * `google_ads` needs `google` — so dropping Google in one round has to drop Google Ads in the
   * next. Twenty-five names and two rounds in practice, so assuming a depth would buy nothing
   * and would be wrong the first time an integration requires an integration that requires one.
   */
  const effective = $derived.by(() => {
    let keep = [...ticked];
    for (;;) {
      const next = keep.filter((name) => requires(name).every((need) => keep.includes(need)));
      if (next.length === keep.length) return next;
      keep = next;
    }
  });

  /** Ticked, but not saveable — the reader asked for it and something it needs is switched off. */
  const dropped = $derived(ticked.filter((name) => !effective.includes(name)));

  /**
   * Ticking an integration on switches on what it needs, transitively. The other direction is
   * left to `effective`: silently re-enabling a module somebody just switched off would be the
   * screen arguing with them, while adding what they clearly meant to add is finishing the
   * sentence they started.
   */
  function pullInRequirements(name: string): void {
    const queue = [...requires(name)];
    while (queue.length) {
      const need = queue.shift()!;
      if (ticked.includes(need)) continue;
      ticked = [...ticked, need];
      queue.push(...requires(need));
    }
  }

  interface Row {
    name: string;
    isHub: boolean;
    locked: boolean;
    needs: string[];
    blocked: boolean;
  }

  function rows(kind: string): Row[] {
    return data.available
      .filter((name: string) => (data.kinds[name] ?? "module") === kind)
      .map((name: string) => ({
        name,
        isHub: name === "companies",
        // Locked (issue #137): needs a license, isn't covered, and isn't already enabled — an
        // enabled-but-uncovered module stays toggleable so it can at least be dropped.
        locked:
          data.licensed.includes(name) &&
          !data.entitled.includes(name) &&
          !data.enabled.includes(name),
        needs: requires(name),
        blocked: ticked.includes(name) && !effective.includes(name),
      }))
      .sort((a: Row, b: Row) => moduleLabel(a.name).localeCompare(moduleLabel(b.name)));
  }

  const moduleRows = $derived(rows("module"));
  const integrationRows = $derived(rows("integration"));

  const names = (keys: string[]) => keys.map(moduleLabel).join(", ");
</script>

<svelte:head>
  <title>{pageTitle(t("settings.modules.title"))}</title>
</svelte:head>

<div class="mb-6">
  <h1 class="mt-2 text-xl font-semibold text-text">{t("settings.modules.title")}</h1>
  <p class="mt-1 text-sm text-text-muted">{t("settings.modules.subtitle")}</p>
</div>

{#snippet row(item: Row)}
  {@const disabled = item.isHub || item.locked}
  <li>
    <label
      class="flex items-start gap-3 rounded-lg border px-3 py-2.5 {item.blocked
        ? 'border-amber-500/40'
        : 'border-border'} {disabled ? 'opacity-70' : 'hover:border-brand/50'}"
    >
      <!-- No `name`: the checkboxes drive `ticked`, and the form posts `effective` from the
           hidden inputs below. Posting the boxes directly would send a set the API refuses. -->
      <input
        type="checkbox"
        value={item.name}
        bind:group={ticked}
        {disabled}
        onchange={(event) => {
          if (event.currentTarget.checked) pullInRequirements(item.name);
        }}
        class="mt-0.5 h-4 w-4 rounded border-border text-brand focus:ring-brand"
      />
      <span class="flex-1">
        <span class="block text-sm font-medium text-text">{moduleLabel(item.name)}</span>
        {#if item.needs.length}
          <!-- Stated on every integration that has one, not only while it blocks: "wat sleept dit
               mee" and "waarom kan ik dit niet aanzetten" are the same sentence read at two
               moments, and only one of them happens after the click. -->
          <span
            class="mt-0.5 block text-xs {item.blocked
              ? 'text-amber-700 dark:text-amber-400'
              : 'text-text-muted'}"
          >
            {t("settings.modules.requires")}: {names(item.needs)}
          </span>
        {/if}
      </span>
      {#if item.isHub}
        <span class="rounded-full bg-surface px-2 py-0.5 text-[11px] text-text-muted">
          {t("settings.modules.always_on")}
        </span>
      {:else if item.locked}
        <span
          class="rounded-full bg-amber-500/10 px-2 py-0.5 text-[11px] text-amber-700 dark:text-amber-400"
          title={lockedHint}
        >
          {t("settings.modules.locked")}
        </span>
      {:else if item.blocked}
        <span
          class="rounded-full bg-amber-500/10 px-2 py-0.5 text-[11px] text-amber-700 dark:text-amber-400"
        >
          {t("settings.modules.unavailable")}
        </span>
      {/if}
    </label>
  </li>
{/snippet}

<form
  method="POST"
  action="?/update"
  use:enhance={busy.wrap("", () => async ({ update }) => {
    // Keep the ticked state after save (docs/UX.md): the default reset would wipe the
    // checkboxes back to their SSR attributes.
    await update({ reset: false });
  })}
  class="max-w-lg space-y-6"
>
  <!-- What actually gets saved: the valid set, not the ticked set. `companies` is always in it —
       it is the hub and its checkbox is disabled, and a disabled checkbox posts nothing. -->
  {#each effective as name (name)}
    <input type="hidden" name="modules" value={name} />
  {/each}

  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="text-sm font-semibold text-text">{t("settings.modules.modules_heading")}</h2>
    <p class="mb-3 mt-1 text-xs text-text-muted">{t("settings.modules.modules_hint")}</p>
    <ul class="space-y-2">
      {#each moduleRows as item (item.name)}{@render row(item)}{/each}
    </ul>
  </section>

  {#if integrationRows.length}
    <section class="rounded-xl border border-border bg-surface-raised p-5">
      <h2 class="text-sm font-semibold text-text">
        {t("settings.modules.integrations_heading")}
      </h2>
      <p class="mb-3 mt-1 text-xs text-text-muted">
        {t("settings.modules.integrations_hint")}
      </p>
      <ul class="space-y-2">
        {#each integrationRows as item (item.name)}{@render row(item)}{/each}
      </ul>
    </section>
  {/if}

  <div>
    <p class="text-xs text-text-muted">{t("settings.modules.hint")}</p>
    {#if dropped.length}
      <!-- Not an error and not a block: the save goes through, and this says what will not be in
           it. The reader fixes it by ticking the module named on the row, at which point this
           line disappears by recomputation. -->
      <p class="mt-2 text-sm text-amber-700 dark:text-amber-400">
        {t("settings.modules.dropped")}: {names(dropped)}
      </p>
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
