<script lang="ts">
  /**
   * The guided setup the Rank Math picker draws instead of an empty list (#435).
   *
   * Rank Math AI Visibility needs four things in the **client's own WordPress**, and none of
   * them is something schakl can do for them. Before this, all four arrived as one boolean and
   * one empty combobox: "there is no credential", "the credential was refused", "the plugin is
   * not installed" and "this client has no brand yet" were the same screen, and two of them
   * were drawn with the wrong sentence.
   *
   * So the API names which prerequisite is the first unmet one and this draws the whole path:
   * what is already done, what to do next in plain words, and the button that goes to the
   * screen — in WordPress, in another browser tab — where it is actually done. A marketeer who
   * has never opened a `wp-admin` is the reader.
   *
   * Two rules from the house style are load-bearing here. Every state carries a **glyph and a
   * word**, never a colour alone (the dev tenant's brand colour is gold, so a coloured dot on
   * its own carries nothing). And a step is expanded **only** when it is the current one: four
   * open paragraphs is a document, and what somebody needs is the one next thing.
   */
  import { t } from "$lib/core/i18n";

  let {
    stage,
    detail = null,
    links = {},
    websiteId = "",
    onrecheck,
    rechecking = false,
  }: {
    /** `app.core.wordpress`'s `STAGE_*` — which prerequisite is the first unmet one. */
    stage: string;
    /** WordPress's own words about the refusal. A quote: rendered, never translated. */
    detail?: string | null;
    /** Deep links into the client's own wp-admin, keyed as the API names them. */
    links?: Record<string, string>;
    /** The client website this WordPress belongs to — the in-app half of every fix. */
    websiteId?: string;
    /** Ask the API again, skipping its cache. */
    onrecheck?: () => void;
    rechecking?: boolean;
  } = $props();

  /**
   * The four prerequisites, in the order somebody completes them, and which stages sit on each.
   *
   * Derived from the stage rather than from four separate booleans on purpose: the API answers
   * the *first* unmet one, so a step's position in this list is what makes "already done"
   * knowable without the API having to prove three things it never asked about.
   */
  const STEPS: { key: string; stages: string[] }[] = [
    {
      key: "wordpress",
      stages: [
        "no_credential",
        "credential_refused",
        "not_administrator",
        "unreachable",
        "site_error",
      ],
    },
    { key: "rankmath", stages: ["rankmath_missing", "rankmath_too_old"] },
    { key: "ai_visibility", stages: ["ai_visibility_unavailable"] },
    { key: "brand", stages: ["no_brands"] },
  ];

  // `ready` matches no step, so `-1` reads as "everything before this is done" — which is what
  // it is. The host does not mount this component at `ready`, but a stage this file has never
  // heard of must degrade to "we cannot place you" rather than to step 1.
  const current = $derived(STEPS.findIndex((step) => step.stages.includes(stage)));
  const stepNumber = $derived(current < 0 ? STEPS.length : current + 1);

  /**
   * What to press, per stage. An **in-app** action goes to the website's page, where the
   * credential lives; an **external** one opens the client's WordPress in a new tab, and says
   * so, because a control that silently leaves the product is one people do not press twice.
   */
  type Action = { href: string; label: string; external: boolean };
  const ACTIONS: Record<string, { link?: string; label: string; site?: boolean }[]> = {
    no_credential: [{ site: true, label: "marketing.picker.connect_wordpress" }],
    credential_refused: [
      { link: "app_passwords", label: "marketing.rankmath_setup.action.new_app_password" },
      { site: true, label: "marketing.rankmath_setup.action.fix_credential" },
    ],
    not_administrator: [
      { link: "app_passwords", label: "marketing.rankmath_setup.action.new_app_password" },
      { site: true, label: "marketing.rankmath_setup.action.fix_credential" },
    ],
    unreachable: [{ site: true, label: "marketing.rankmath_setup.action.check_connection" }],
    site_error: [{ site: true, label: "marketing.rankmath_setup.action.check_connection" }],
    rankmath_missing: [
      { link: "plugins", label: "marketing.rankmath_setup.action.install_rankmath" },
    ],
    rankmath_too_old: [
      { link: "plugins", label: "marketing.rankmath_setup.action.update_rankmath" },
    ],
    ai_visibility_unavailable: [
      { link: "ai_visibility", label: "marketing.rankmath_setup.action.open_ai_visibility" },
    ],
    no_brands: [{ link: "ai_visibility", label: "marketing.rankmath_setup.action.create_brand" }],
  };

  const actions = $derived(
    (ACTIONS[stage] ?? [])
      .map((spec): Action | null => {
        if (spec.site) {
          // Only offered when we know which website: without one there is no page to send
          // somebody to, and a link that always refuses is a broken control (#253).
          return websiteId
            ? { href: `/websites/${websiteId}`, label: spec.label, external: false }
            : null;
        }
        const href = spec.link ? links[spec.link] : undefined;
        return href ? { href, label: spec.label, external: true } : null;
      })
      .filter((action): action is Action => action !== null),
  );

  // Nothing to re-ask before a credential exists — the answer would be the same sentence.
  const canRecheck = $derived(Boolean(onrecheck) && stage !== "no_credential");
</script>

<div class="space-y-2 rounded-lg border border-border bg-surface-raised p-3">
  <div>
    <p class="text-sm font-medium text-text">{t("marketing.rankmath_setup.title")}</p>
    <p class="text-xs text-text-muted">
      {t("marketing.rankmath_setup.intro", {
        step: String(stepNumber),
        total: String(STEPS.length),
      })}
    </p>
  </div>

  <ol class="space-y-1.5">
    {#each STEPS as step, index (step.key)}
      {@const done = current < 0 || index < current}
      {@const active = index === current}
      <li class="text-sm">
        <div class="flex items-start gap-2">
          <span
            class={done
              ? "text-emerald-600 dark:text-emerald-400"
              : active
                ? "text-text"
                : "text-text-muted"}
            aria-hidden="true">{done ? "✓" : active ? "→" : "○"}</span
          >
          <span class={active ? "font-medium text-text" : "text-text-muted"}>
            {t(`marketing.rankmath_setup.step.${step.key}`)}
          </span>
          <span class="sr-only">
            {t(
              done
                ? "marketing.rankmath_setup.state.done"
                : active
                  ? "marketing.rankmath_setup.state.current"
                  : "marketing.rankmath_setup.state.todo",
            )}
          </span>
        </div>

        {#if active}
          <!-- Only the step somebody is on unfolds: its sentence, what to press, and — last,
               small, and untranslated — whatever the site itself said, which is the line an
               admin will match against their own log. -->
          <div class="ml-6 mt-1 space-y-2">
            <p class="text-sm text-text-muted">{t(`marketing.rankmath_setup.${stage}`)}</p>
            {#each actions as action (action.href + action.label)}
              <a
                href={action.href}
                target={action.external ? "_blank" : undefined}
                rel={action.external ? "noreferrer" : undefined}
                class="block text-sm font-medium text-brand hover:underline"
              >
                {t(action.label)}
                {#if action.external}
                  <span class="font-normal text-text-muted">
                    · {t("marketing.rankmath_setup.opens_wordpress")}
                  </span>
                {/if}
              </a>
            {/each}
            {#if detail}
              <p class="text-xs text-text-muted">
                {t("marketing.rankmath_setup.detail_label")}
                <span class="font-mono" title={detail}>{detail}</span>
              </p>
            {/if}
          </div>
        {/if}
      </li>
    {/each}
  </ol>

  {#if canRecheck}
    <button
      type="button"
      class="text-sm font-medium text-brand hover:underline disabled:opacity-60"
      disabled={rechecking}
      onclick={() => onrecheck?.()}
    >
      {t(rechecking ? "marketing.picker.loading" : "marketing.rankmath_setup.action.recheck")}
    </button>
  {/if}
</div>
