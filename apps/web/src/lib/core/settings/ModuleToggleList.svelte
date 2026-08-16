<script lang="ts">
  /**
   * The list of things this workspace runs, of one kind — modules on one screen, integrations on
   * the other (issue #378, CLAUDE.md §6a).
   *
   * They were one form with one Save, and the shape said they were the same decision. They are
   * not. A module is a capability we provide: switch it on and it works. An integration is a
   * conversation with somebody else's service: switching it on gets you a screen with an empty
   * credential box, and it can stop working tomorrow because a token expired at the other end.
   * Those are answered by different people at different moments, so they are two screens — each
   * at the head of the group of settings it governs, which is what finally stops the word
   * "Modules" naming both the switch and, fourteen cards further down, a different collection.
   *
   * **One component, because the rule spans both screens.** `ensure_requirements_met` is a
   * whole-set rule: Cloudflare needs Domeinen, and Domeinen lives on the other screen. So each
   * screen computes `effective` over the *entire* enabled set and posts the entire set, and names
   * what it is dropping **even when the casualty is not on this screen** — switching Domeinen off
   * under Modules says "Cloudflare and OXXA go too", by name, before you press Save. Without that
   * the reader gets the API's 409, or worse, a silent removal on a screen they were not looking at.
   *
   * The requirement is *derived*, not validated: the API refuses an invalid set anyway
   * (`ensure_requirements_met`), and a backstop should never be the thing a user meets first.
   */
  import { ArrowRight } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";
  import { moduleDescription, moduleLabel } from "$lib/core/registry";
  import { settingsScreenForModule } from "$lib/core/settings-nav";
  import UpgradeModal from "$lib/core/ui/UpgradeModal.svelte";

  let {
    names,
    here = $bindable(),
    ticked,
    onenable,
    kind,
    hub = null,
    licensed,
    entitled,
    enabled,
    requires,
    deployment = "self_hosted",
    isInstanceOwner = false,
    dropped,
  }: {
    /** The names of this screen's own kind, unsorted. */
    names: string[];
    /**
     * This group's own selection — and *only* this group's. `bind:group` rebuilds the array it is
     * bound to from the checkboxes on the page, so binding the whole `enabled_modules` list here
     * would delete the other screen's half on the first click.
     */
    here: string[];
    /** The whole ticked set, read-only: what a row needs to know is whether it is on at all. */
    ticked: string[];
    /** Ask the host to switch on what `name` requires, in whichever half holds it. */
    onenable: (name: string) => void;
    kind: "module" | "integration";
    /** The one name that cannot be switched off (the hub), or `null` on the other screen. */
    hub?: string | null;
    licensed: string[];
    entitled: string[];
    enabled: string[];
    requires: Record<string, string[]>;
    deployment?: string;
    isInstanceOwner?: boolean;
    /** Ticked but not saveable, computed by the host over the whole set. */
    dropped: string[];
  } = $props();

  const requiresOf = (name: string): string[] => requires[name] ?? [];

  interface Row {
    name: string;
    label: string;
    description: string;
    isHub: boolean;
    locked: boolean;
    needs: string[];
    blocked: boolean;
    /** Where this is configured, when it has exactly one screen of its own. */
    href: string | null;
  }

  const rows = $derived.by((): Row[] =>
    names
      .map((name) => {
        const screen = settingsScreenForModule(name);
        return {
          name,
          label: moduleLabel(name),
          description: moduleDescription(name),
          isHub: name === hub,
          // Locked (issue #137): needs a licence, isn't covered, and isn't already enabled — an
          // enabled-but-uncovered one stays toggleable so it can at least be dropped.
          locked: licensed.includes(name) && !entitled.includes(name) && !enabled.includes(name),
          needs: requiresOf(name),
          blocked: dropped.includes(name),
          href: screen?.href ?? null,
        };
      })
      .sort((a, b) => a.label.localeCompare(b.label)),
  );

  /** What the upgrade dialog is explaining, and whether it is up (#137). */
  let upgradeFeature = $state("");
  let upgradeOpen = $state(false);
</script>

<section class="rounded-xl border border-border bg-surface-raised p-5">
  <h2 class="text-sm font-semibold text-text">
    {t(
      kind === "module"
        ? "settings.modules.modules_heading"
        : "settings.modules.integrations_heading",
    )}
  </h2>
  <p class="mb-4 mt-1 text-sm text-text-muted">
    {t(kind === "module" ? "settings.modules.modules_hint" : "settings.modules.integrations_hint")}
  </p>

  <ul class="space-y-2">
    {#each rows as item (item.name)}
      {@const disabled = item.isHub || item.locked}
      <li
        class="rounded-lg border {item.blocked ? 'border-amber-500/40' : 'border-border'} {disabled
          ? 'opacity-80'
          : ''}"
      >
        <div class="flex items-start gap-3 px-3 py-2.5">
          <!-- The label wraps only the checkbox and the name, never the row: the row now holds a
               link, and a link inside a label is a control that does two things on one click. -->
          <label class="flex min-w-0 flex-1 items-start gap-3">
            <!-- No `name`: the checkboxes drive `ticked`, and the form posts `effective` from the
                 host's hidden inputs. Posting the boxes directly would send a set the API refuses. -->
            <input
              type="checkbox"
              value={item.name}
              bind:group={here}
              {disabled}
              onchange={(event) => {
                if (event.currentTarget.checked) onenable(item.name);
              }}
              class="mt-0.5 h-4 w-4 shrink-0 rounded border-border text-brand focus:ring-brand"
            />
            <span class="min-w-0 flex-1">
              <span class="block text-sm font-medium text-text">{item.label}</span>
              {#if item.description}
                <span class="mt-0.5 block text-xs text-text-muted">{item.description}</span>
              {/if}
              {#if item.needs.length}
                <!-- Stated on everything that has a requirement, not only while it blocks: "what
                     does this drag in" and "why can't I switch this on" are the same sentence read
                     at two moments, and only one of them happens after the click. -->
                <span
                  class="mt-0.5 block text-xs {item.blocked
                    ? 'text-amber-700 dark:text-amber-400'
                    : 'text-text-muted'}"
                >
                  {t("settings.modules.requires")}: {item.needs.map(moduleLabel).join(", ")}
                </span>
              {/if}
            </span>
          </label>

          <span class="flex shrink-0 items-center gap-2">
            {#if item.isHub}
              <span class="rounded-full bg-surface px-2 py-0.5 text-[11px] text-text-muted">
                {t("settings.modules.always_on")}
              </span>
            {:else if item.locked}
              <!-- #137: a lock the org itself can open explains itself on click. A dead pill with
                   a title attribute was the one thing this screen could not be asked. -->
              <button
                type="button"
                onclick={() => {
                  upgradeFeature = item.label;
                  upgradeOpen = true;
                }}
                class="rounded-full bg-amber-500/10 px-2 py-0.5 text-[11px] text-amber-700
                  hover:bg-amber-500/20 dark:text-amber-400"
              >
                {t("settings.modules.locked")}
              </button>
            {:else if item.blocked}
              <span
                class="rounded-full bg-amber-500/10 px-2 py-0.5 text-[11px] text-amber-700 dark:text-amber-400"
              >
                {t("settings.modules.unavailable")}
              </span>
            {/if}

            {#if item.href && ticked.includes(item.name) && !item.blocked}
              <!-- Only once it is on, because the screen behind it does not exist until then.
                   This is the half the old screen never said: an integration switched on is an
                   empty credential box somewhere else, and nothing pointed at it. -->
              <a
                href={item.href}
                class="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs font-medium
                  text-brand hover:bg-brand/10"
                data-sveltekit-preload-data="hover"
              >
                {t(kind === "module" ? "settings.modules.configure" : "settings.modules.connect")}
                <ArrowRight size={13} aria-hidden="true" />
              </a>
            {/if}
          </span>
        </div>
      </li>
    {/each}
  </ul>
</section>

<!-- Mounted only while it is up: a Modal registers its Escape handler whether open or not, and a
     screen with a permanently-mounted dialog answers a keystroke meant for something else. -->
{#if upgradeOpen}
  <UpgradeModal bind:open={upgradeOpen} feature={upgradeFeature} {deployment} {isInstanceOwner} />
{/if}
