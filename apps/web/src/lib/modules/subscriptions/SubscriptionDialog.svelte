<script lang="ts">
  /**
   * Record an agreement **without leaving the record you are looking at**.
   *
   * The client hub's Abonnementen card offered a link to `/subscriptions?company=…&new=1`: the
   * client was carried through and the way back was not, which is the one thing every other
   * "record something about this client" on that page — a contactmoment, hours (#402), a
   * contact person — does not do. This is the module's own form (`SubscriptionForm`, the same
   * one `/subscriptions` mounts) in a modal, hosted by whatever page shows the client.
   *
   * It does not fetch anything until it is opened: the pickers are seven small reads, and most
   * visits to a client never record an agreement (#314, `docs/PERFORMANCE.md`). It does not
   * close on a refusal either — the form keeps the typing and prints the reason.
   *
   * **Host contract:** the page spreads `subscriptionActions` (`actions.server.ts`).
   */
  import { untrack } from "svelte";

  import { t } from "$lib/core/i18n";
  import Modal from "$lib/core/ui/Modal.svelte";
  import { toastSuccess } from "$lib/core/ui/toast.svelte";

  import SubscriptionForm from "./SubscriptionForm.svelte";
  import type { SubscriptionFormLookups } from "./types";

  let {
    open = $bindable(false),
    companyId,
    locale,
  }: {
    open?: boolean;
    /** Preselected in the form's client picker — a default that is visible and changeable. */
    companyId: string;
    locale: string;
  } = $props();

  let lookups = $state<SubscriptionFormLookups | null>(null);
  let phase = $state<"idle" | "loading" | "failed">("idle");
  /** Bumped per open, so a second visit gets an empty form rather than the last one's leftovers. */
  let session = $state(0);

  async function read<T>(url: string, fallback: T): Promise<T> {
    const response = await fetch(url, { headers: { accept: "application/json" } });
    if (!response.ok) return fallback;
    return (await response.json()) as T;
  }

  async function ensureLookups(): Promise<void> {
    if (lookups || phase === "loading") return;
    phase = "loading";
    try {
      type Page<T> = { items: T[] };
      type Row = { id: string; name: string; status?: string | null; company_id?: string | null };
      const [companies, projects, types, templates, definitions, companyDefinitions, invoicing] =
        await Promise.all([
          read<Page<Row>>("/api/v1/companies?limit=200&offset=0&count=false&sort=name", {
            items: [],
          }),
          read<Page<Row>>("/api/v1/projects?limit=200&offset=0&count=false", { items: [] }),
          read<SubscriptionFormLookups["types"]>("/api/v1/subscriptions/types", []),
          read<SubscriptionFormLookups["templates"]>("/api/v1/subscriptions/templates", []),
          read<SubscriptionFormLookups["definitions"]>(
            "/api/v1/custom-fields/definitions?entity_type=subscription",
            [],
          ),
          read<SubscriptionFormLookups["companyDefinitions"]>(
            "/api/v1/custom-fields/definitions?entity_type=company",
            [],
          ),
          // Only to name the inherited level in the form's "follow the organisation" hint. A
          // caller who cannot read invoicing settings simply gets the seeded default there.
          read<{ auto_invoice_mode?: SubscriptionFormLookups["orgAutoInvoiceMode"] } | null>(
            "/api/v1/invoicing/settings",
            null,
          ),
        ]);
      lookups = {
        companies: companies.items.map((c) => ({ id: c.id, name: c.name, status: c.status })),
        projects: projects.items.map((p) => ({
          id: p.id,
          name: p.name,
          status: p.status,
          company_id: p.company_id,
        })),
        types,
        templates,
        definitions,
        companyDefinitions,
        orgAutoInvoiceMode: invoicing?.auto_invoice_mode ?? null,
      };
      phase = "idle";
    } catch {
      // A dialog that spins forever is worse than one that says so and offers the page that
      // does not need this call (#253 — a control must not silently refuse).
      phase = "failed";
    }
  }

  $effect(() => {
    if (!open) return;
    // `ensureLookups` reads the very state it writes; tracking that would re-run this effect on
    // its own assignment and fire a second fetch on the first open.
    untrack(() => {
      session += 1;
      void ensureLookups();
    });
  });
</script>

<Modal bind:open title={t("subscriptions.add")}>
  {#if lookups}
    {#key session}
      <SubscriptionForm
        {lookups}
        {locale}
        defaultCompanyId={companyId}
        action="?/createSubscription"
        projectAction="?/createSubscriptionProject"
        typeAction="?/createSubscriptionType"
        companyAction="?/createSubscriptionCompany"
        oncancel={() => (open = false)}
        onsaved={() => {
          open = false;
          toastSuccess(t("subscriptions.saved"));
        }}
      />
    {/key}
  {:else if phase === "failed"}
    <p class="text-sm text-text-muted">{t("subscriptions.dialog.load_failed")}</p>
    <a
      href={`/subscriptions?company=${companyId}&new=1`}
      class="mt-3 inline-block text-sm font-medium text-brand hover:underline"
    >
      {t("subscriptions.dialog.open_list")}
    </a>
  {:else}
    <p class="text-sm text-text-muted">{t("common.loading")}</p>
  {/if}
</Modal>
