<script lang="ts" module>
  /**
   * Where a subject's own record lives, and what to call the link to it.
   *
   * The register lists logins by subject **type**, so the destination is per type — the
   * notifications module's `HREF_FOR_ENTITY` shape, one screen over. A type with no entry
   * simply gets no link rather than a guessed URL: the register is a register, and "open the
   * person" is the one thing it hands off (#406 — do not grow a second editor here).
   */
  const SUBJECT_LINK: Record<string, { href: (id: string) => string; labelKey: string }> = {
    contact: { href: (id) => `/contacts/${id}`, labelKey: "portal.register.open_contact" },
  };
</script>

<script lang="ts">
  /**
   * **Klantlogins** (#406) — who at our clients can sign in, and is their access still live?
   *
   * Until this existed, a client login was reachable from exactly one place: the contact it
   * belonged to. So nobody could say how many logins an agency had without opening every
   * contact, a client's employee who left kept a live login until somebody remembered which row
   * it was, and an invite nobody ever used was invisible.
   *
   * It is a **register with the access actions on it**, not a second editor: the person is
   * edited on their own record, which every row links to. Each action is gated on the key the
   * call actually makes (#310) — managing the login is the section's own
   * `members.member.write`, while signing in as the client is `portal.login.impersonate`, which
   * most staff will not hold.
   */
  import { enhance } from "$app/forms";
  import { ExternalLink, Lock, Mail, UserCheck, UserX, VenetianMask } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import ActionsMenu, { type ActionItem } from "$lib/core/ui/ActionsMenu.svelte";
  import Avatar from "$lib/core/ui/Avatar.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import LockedButton from "$lib/core/ui/LockedButton.svelte";

  import type { PortalLoginRow, PortalRegisterData } from "./types";

  let {
    data,
    form,
  }: {
    data: PortalRegisterData;
    /** The host page's whole `form` result — see `PortalCard` for why this stays loose. */
    form?: Record<string, unknown> | null;
  } = $props();

  const portalError = $derived(typeof form?.portalError === "string" ? form.portalError : null);
  const inviteMailFailed = $derived(form?.portalSaved === true && form?.portalEmail === false);

  const busy = new InFlight();

  // One hidden form per action that needs no dialog, the users page's `activate` pattern:
  // `ActionsMenu` takes handlers rather than forms, and a menu item cannot be a submit button.
  let resendForm: HTMLFormElement | undefined = $state();
  let enableForm: HTMLFormElement | undefined = $state();
  let pending = $state({ entity_type: "", subject_id: "" });
  let confirmDisable = $state(false);
  let confirmImpersonate = $state(false);
  let impersonating = $state<PortalLoginRow | null>(null);

  function fire(row: PortalLoginRow, target: () => HTMLFormElement | undefined) {
    pending = { entity_type: row.entity_type, subject_id: row.subject_id };
    // After the hidden inputs have taken the new value — the same one-tick wait the roster's
    // Activeren does.
    setTimeout(() => target()?.requestSubmit(), 0);
  }

  function personName(row: PortalLoginRow): string {
    return row.name || row.email;
  }

  /** `clients` carries a server-side default, so the generated type makes it optional. */
  const clientsOf = (row: PortalLoginRow) => row.clients ?? [];

  function rowActions(row: PortalLoginRow): ActionItem[] {
    const items: ActionItem[] = [];
    if (row.status === "invited") {
      items.push({
        label: t("portal.resend"),
        icon: Mail,
        disabled: busy.active,
        onclick: () => fire(row, () => resendForm),
      });
    }
    if (row.status === "disabled") {
      // Handing access back destroys nothing, so it does not confirm — the roster's rule for
      // Activeren, and the same act one audience over.
      items.push({
        label: t("portal.reenable"),
        icon: UserCheck,
        disabled: busy.active,
        onclick: () => fire(row, () => enableForm),
      });
    } else {
      // Disabling *does* confirm here, where the card on the contact's own page does not: on a
      // register the rows are adjacent and about different clients, so the cost of the wrong
      // press is somebody else's access — and the page you were on is no longer the answer to
      // "who was that?".
      items.push({
        label: t("portal.disable"),
        icon: UserX,
        danger: true,
        onclick: () => {
          pending = { entity_type: row.entity_type, subject_id: row.subject_id };
          confirmDisable = true;
        },
      });
    }
    if (data.canImpersonate && row.status !== "disabled") {
      items.push({
        label: t("portal.impersonate"),
        icon: VenetianMask,
        onclick: () => {
          pending = { entity_type: row.entity_type, subject_id: row.subject_id };
          impersonating = row;
          confirmImpersonate = true;
        },
      });
    }
    const link = SUBJECT_LINK[row.entity_type];
    if (link)
      items.push({ label: t(link.labelKey), icon: ExternalLink, href: link.href(row.subject_id) });
    return items;
  }

  const badgeClass = (status: PortalLoginRow["status"]) =>
    status === "active"
      ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300"
      : status === "invited"
        ? "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300"
        : "text-text-muted ring-1 ring-inset ring-border";
</script>

<section class="mt-10">
  <div class="mb-3 flex flex-wrap items-center gap-2">
    <h2 class="text-lg font-semibold text-text">{t("portal.register.title")}</h2>
    {#if data.locked}
      <span
        class="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium
          text-text-muted ring-1 ring-inset ring-border"
      >
        <Lock size={11} aria-hidden="true" />
        {t("portal.locked_badge")}
      </span>
    {:else if data.logins.length > 0}
      <!-- The number is `logins.length` and never a total the API sent beside them: a count
           computed apart from the rows is how a restricted admin reads "2" over a list of
           one (#285). -->
      <span class="rounded-full bg-surface px-2 py-0.5 text-[11px] font-medium text-text-muted">
        {data.logins.length}
      </span>
    {/if}
  </div>
  <p class="mb-4 text-sm text-text-muted">
    {data.locked ? t("portal.register.locked_hint") : t("portal.register.subtitle")}
  </p>

  {#if portalError}
    <p
      class="mb-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-600 dark:bg-red-950 dark:text-red-400"
    >
      {t(portalError)}
    </p>
  {/if}
  {#if inviteMailFailed}
    <p
      class="mb-4 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-300"
    >
      {t("portal.email_not_sent")}
    </p>
  {/if}

  {#if data.locked}
    <!-- Something the agency can buy, so the affordance stays and says how (#137). -->
    <LockedButton
      label={t("portal.register.locked")}
      feature={t("module.portal.label")}
      title={t("portal.register.locked_hint")}
      deployment={data.deployment}
      isInstanceOwner={data.isInstanceOwner}
    />
  {:else if data.logins.length === 0}
    <p
      class="rounded-xl border border-dashed border-border bg-surface-raised p-8 text-center text-sm text-text-muted"
    >
      {t("portal.register.empty")}
    </p>
  {:else}
    <ul class="divide-y divide-border rounded-xl border border-border bg-surface-raised">
      {#each data.logins as row (row.user_id)}
        {@const link = SUBJECT_LINK[row.entity_type]}
        {@const clients = clientsOf(row)}
        <li class="flex items-center gap-3 px-4 py-3 first:rounded-t-xl last:rounded-b-xl">
          <Avatar name={row.name} email={row.email} size="md" />
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-2">
              {#if link}
                <a
                  href={link.href(row.subject_id)}
                  class="truncate font-medium text-text hover:text-brand">{personName(row)}</a
                >
              {:else}
                <span class="truncate font-medium text-text">{personName(row)}</span>
              {/if}
              <span
                class="rounded-full px-2 py-0.5 text-[11px] font-medium {badgeClass(row.status)}"
              >
                {t(`portal.status.${row.status}`)}
              </span>
            </div>
            <p class="truncate text-sm text-text-muted">{row.email}</p>
            <p class="mt-0.5 truncate text-xs text-text-muted">
              {#if clients.length === 0}
                <!-- A login attached to no client sees nothing at all (#274's empty horizon):
                     worth saying here, where somebody is looking at the access. -->
                {t("portal.register.no_client")}
              {:else}
                {#each clients as client, i (client.id)}{i > 0 ? " · " : ""}<a
                    href={`/companies/${client.id}`}
                    class="hover:text-brand">{client.name}</a
                  >{/each}
              {/if}
            </p>
          </div>
          <ActionsMenu items={rowActions(row)} />
        </li>
      {/each}
    </ul>
  {/if}
</section>

<!-- The two menu items that need no dialog. `reset: true`: neither form has a field anybody
     typed into, and the next press writes the hidden values afresh (docs/UX.md). -->
<form
  method="POST"
  action="?/portalResend"
  use:enhance={busy.wrap(
    "resend",
    () =>
      ({ update }) =>
        update({ reset: true }),
  )}
  bind:this={resendForm}
  class="hidden"
>
  <input type="hidden" name="entity_type" value={pending.entity_type} />
  <input type="hidden" name="subject_id" value={pending.subject_id} />
</form>

<form
  method="POST"
  action="?/portalEnable"
  use:enhance={busy.wrap(
    "enable",
    () =>
      ({ update }) =>
        update({ reset: true }),
  )}
  bind:this={enableForm}
  class="hidden"
>
  <input type="hidden" name="entity_type" value={pending.entity_type} />
  <input type="hidden" name="subject_id" value={pending.subject_id} />
</form>

<ConfirmDialog
  bind:open={confirmDisable}
  title={t("portal.disable")}
  message={t("portal.register.disable_confirm")}
  consequences={[
    t("portal.register.disable_effect_login"),
    t("portal.register.disable_effect_kept"),
    t("portal.register.disable_effect_reversible"),
  ]}
  confirmLabel={t("portal.disable")}
  action="?/portalDisable"
  fields={{ entity_type: pending.entity_type, subject_id: pending.subject_id }}
/>

<!-- Destroys nothing, so a primary confirm rather than the default red one. -->
<ConfirmDialog
  bind:open={confirmImpersonate}
  title={t("portal.impersonate")}
  message={t("portal.impersonate_confirm", {
    name: impersonating ? personName(impersonating) : "",
  })}
  confirmLabel={t("portal.impersonate")}
  variant="primary"
  action="?/portalImpersonate"
  fields={{ entity_type: pending.entity_type, subject_id: pending.subject_id }}
/>
