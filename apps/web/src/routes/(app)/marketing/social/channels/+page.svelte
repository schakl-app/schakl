<script lang="ts">
  /**
   * Whose is what: every Page, Instagram account and ad account a token reaches, and the
   * client each was linked to.
   *
   * **Nothing is ever picked for anybody.** A token sees every client an agency ever worked
   * for; which of those are managed here is a person's decision, made per row. So the screen
   * opens on the rows still waiting for that decision, and a row that cannot work — a Page
   * the token may not publish to, an Instagram account with no Page behind it — says so on
   * the row, in the viewer's language, before anybody plans a post for it.
   *
   * What the dialog *may* do is save typing: a channel called "Nova Fietsen" opens with the
   * client of that name filled in, marked as a suggestion, and a Page offers its own
   * Instagram account in the same step. Both are a filled-in form, never a saved decision.
   */
  import ExternalLink from "@lucide/svelte/icons/external-link";
  import Link2 from "@lucide/svelte/icons/link-2";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";

  import { enhance } from "$app/forms";
  import FilterBar from "$lib/core/filters/FilterBar.svelte";
  import type { FilterDef } from "$lib/core/filters/types";
  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import { pageTitle } from "$lib/core/title";
  import ActionsMenu, { type ActionItem } from "$lib/core/ui/ActionsMenu.svelte";
  import Avatar from "$lib/core/ui/Avatar.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import PageHeader from "$lib/core/ui/PageHeader.svelte";
  import Pagination from "$lib/core/ui/Pagination.svelte";
  import StateMark from "$lib/core/ui/StateMark.svelte";
  import ChannelMark from "$lib/integrations/meta/ChannelMark.svelte";
  import { CHANNEL_COLUMNS } from "$lib/integrations/meta/columns";
  import { errorText, kindLabel } from "$lib/integrations/meta/format";
  import CompanyQuickCreate from "$lib/modules/companies/CompanyQuickCreate.svelte";
  import { companyArchivedLabel, splitCompanyOptions } from "$lib/modules/companies/picker";

  let { data, form } = $props();

  type Asset = (typeof data.assets)[number];
  type FilterKey = "q" | "kind" | "state";

  const busy = new InFlight();
  const PICKER_SLOT = "meta-channel-company";

  const waiting = $derived(data.status.channels_unlinked + (data.status.ad_accounts_unlinked ?? 0));

  const filterDefs: FilterDef<FilterKey>[] = $derived([
    { kind: "search", key: "q", placeholder: t("meta.channels.search_placeholder") },
    {
      kind: "select",
      key: "kind",
      placeholder: t("meta.channels.all_kinds"),
      options: [
        { value: "page", label: kindLabel("page") },
        { value: "instagram", label: kindLabel("instagram") },
        { value: "ad_account", label: kindLabel("ad_account") },
      ],
    },
    {
      kind: "pills",
      key: "state",
      // The load resolves the default from what is waiting, so the pill that shows itself
      // selected is the one the list is actually on.
      value: data.state,
      options: [
        {
          value: "unlinked",
          // Counts what the list under it holds: channels and ad accounts alike.
          label:
            waiting > 0
              ? `${t("meta.channels.unlinked")} (${waiting})`
              : t("meta.channels.unlinked"),
        },
        { value: "linked", label: t("meta.channels.linked") },
        { value: "all", label: t("meta.filter.all") },
      ],
    },
  ]);

  const table = createTableLayout<Asset>({
    all: () => CHANNEL_COLUMNS,
    pref: () => data.table.pref,
    sort: () => null,
    cells: () => ({
      asset: assetCell,
      kind: kindCell,
      client: clientCell,
      relation: relationCell,
      state: stateCell,
    }),
  });

  // --- linking -------------------------------------------------------------------------------
  let linking = $state<Asset | null>(null);
  let linkOpen = $state(false);
  let chosen = $state("");
  let own = $state(false);
  let quickCreateOpen = $state(false);
  let quickCreateName = $state("");
  let unlinking = $state<Asset | null>(null);
  let unlinkOpen = $state(false);

  const companyPicker = $derived(splitCompanyOptions(data.companies, { selectedId: chosen }));

  let suggested = $state(false);
  let also = $state<string[]>([]);

  const plain = (value: string) => value.trim().toLocaleLowerCase();

  /**
   * The one client this channel is obviously named after, or nothing. Exactly one candidate:
   * two clients that both fit is a question, and a question is left to the person.
   */
  function namesake(asset: Asset): string {
    const name = plain(asset.name);
    const fits = data.companies.filter((company) => {
      const candidate = plain(company.name);
      return candidate.length > 2 && (name === candidate || name.startsWith(`${candidate} `));
    });
    return fits.length === 1 ? fits[0].id : "";
  }

  /** The channels that belong with this one: a Page's Instagram account, and the reverse. */
  const companions = $derived(
    linking
      ? data.assets.filter(
          (asset) =>
            asset.id !== linking?.id &&
            !asset.active &&
            asset.kind !== "ad_account" &&
            (asset.linked_page_id === linking?.id || linking?.linked_page_id === asset.id),
        )
      : [],
  );

  function startLink(asset: Asset): void {
    linking = asset;
    chosen = asset.company_id ?? "";
    suggested = false;
    if (!chosen && !asset.active) {
      chosen = namesake(asset);
      suggested = chosen !== "";
    }
    own = asset.active && !asset.company_id;
    also = data.assets
      .filter(
        (other) =>
          other.id !== asset.id &&
          !other.active &&
          other.kind !== "ad_account" &&
          (other.linked_page_id === asset.id || asset.linked_page_id === other.id),
      )
      .map((other) => other.id);
    linkOpen = true;
  }

  // A client made through the ＋ selects itself; the slot keeps a sibling picker from
  // taking the selection (docs/UX.md).
  $effect(() => {
    const created = form && "inlineCreated" in form ? form.inlineCreated : null;
    if (created?.slot === PICKER_SLOT && created.id) {
      chosen = created.id;
      own = false;
    }
  });

  function rowActions(asset: Asset): ActionItem[] {
    const items: ActionItem[] = [
      {
        label: asset.active ? t("meta.channels.change_client") : t("meta.channels.link"),
        icon: Link2,
        onclick: () => startLink(asset),
      },
    ];
    if (asset.meta_url) {
      items.push({
        label: t("meta.channels.open_at_meta"),
        icon: ExternalLink,
        href: asset.meta_url,
      });
    }
    if (asset.active) {
      items.push({
        label: t("meta.channels.unlink"),
        danger: true,
        onclick: () => {
          unlinking = asset;
          unlinkOpen = true;
        },
      });
    }
    return items;
  }
</script>

<svelte:head>
  <title>{pageTitle(t("meta.channels.title"))}</title>
</svelte:head>

<PageHeader title={t("meta.channels.title")}>
  {#snippet subtitle()}{t("meta.channels.subtitle")}{/snippet}
  {#snippet actions()}
    {#if data.canManage}
      <a
        href="/settings/meta"
        class="inline-flex items-center gap-1.5 rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-text hover:border-text-muted"
      >
        {t("meta.channels.manage_connection")}
      </a>
    {/if}
  {/snippet}
</PageHeader>

{#if form && "error" in form && form.error}
  <p class="mb-4 text-sm text-red-600 dark:text-red-400" role="alert">{t(form.error)}</p>
{/if}
{#if data.loadError}
  <p class="mb-4 text-sm text-red-600 dark:text-red-400" role="alert">{t(data.loadError)}</p>
{/if}

<FilterBar filters={filterDefs} idPrefix="channel-filter" />

{#snippet assetCell(asset: Asset)}
  <span class="flex min-w-0 items-center gap-2.5">
    <Avatar name={asset.name} avatarUrl={asset.picture_url} size="md" />
    <span class="min-w-0">
      <span class="block truncate font-medium text-text">{asset.name}</span>
      <span class="block truncate text-xs text-text-muted">
        {asset.username ? `@${asset.username}` : asset.meta_id}
        {#if asset.kind === "instagram" && asset.linked_page_name}
          · {t("meta.channels.via_page", { name: asset.linked_page_name })}
        {/if}
        {#if asset.kind === "ad_account" && asset.currency}· {asset.currency}{/if}
      </span>
    </span>
  </span>
{/snippet}

{#snippet kindCell(asset: Asset)}
  <span class="flex items-center gap-1.5 truncate text-text-muted">
    {#if asset.kind !== "ad_account"}<ChannelMark
        channel={asset.kind === "instagram" ? "instagram" : "facebook"}
        size={14}
      />{/if}
    <span class="truncate">{kindLabel(asset.kind)}</span>
  </span>
{/snippet}

{#snippet clientCell(asset: Asset)}
  {#if asset.company_id && asset.company_name}
    <a href={`/companies/${asset.company_id}`} class="block truncate text-text hover:text-brand"
      >{asset.company_name}</a
    >
  {:else if asset.active}
    <span class="block truncate text-text-muted">{t("meta.own_channels")}</span>
  {:else}
    <span class="block truncate italic text-text-muted">{t("meta.channels.nobody")}</span>
  {/if}
{/snippet}

{#snippet relationCell(asset: Asset)}
  <span class="block truncate text-text-muted">{t(`meta.relation.${asset.relation}`)}</span>
{/snippet}

{#snippet stateCell(asset: Asset)}
  <!-- cells:wrap — the line under the chip is the reason a channel cannot publish, and the
       reason is the instruction: cut to an ellipsis it names a problem and withholds the fix. -->
  {#if asset.status === "error"}
    <StateMark state="late" label={t("meta.channels.state_error")} variant="chip" />
    {#if asset.last_error}
      <span class="mt-1 block break-words text-xs text-text-muted"
        >{errorText(asset.last_error)}</span
      >
    {/if}
  {:else if !asset.active}
    <span class="text-xs text-text-muted">{t("meta.channels.state_found")}</span>
  {:else if asset.kind !== "ad_account" && !asset.can_publish}
    <StateMark state="today" label={t("meta.channels.state_blocked")} variant="chip" />
    {#if asset.blocked_by}
      <span class="mt-1 block text-xs text-text-muted"
        >{t(asset.blocked_by, { name: asset.name })}</span
      >
    {/if}
  {:else}
    <StateMark state="ok" label={t("meta.channels.state_ready")} variant="chip" />
    {#if asset.last_verified_at}
      <span class="mt-1 block text-xs text-text-muted"
        >{t("meta.channels.seen", { when: fmtDateTime(asset.last_verified_at) })}</span
      >
    {/if}
  {/if}
{/snippet}

{#snippet rowMenu(asset: Asset)}
  {#if data.canManage}
    {#if !asset.active}
      <Button type="button" size="xs" variant="secondary" onclick={() => startLink(asset)}>
        {t("meta.channels.link")}
      </Button>
    {:else}
      <ActionsMenu items={rowActions(asset)} compact />
    {/if}
  {/if}
{/snippet}

{#snippet mobileRow(asset: Asset)}
  <span class="min-w-0 flex-1">
    <span class="flex items-center gap-1.5 text-sm font-medium text-text">
      <span class="truncate">{asset.name}</span>
      {#if asset.kind !== "ad_account"}<ChannelMark
          channel={asset.kind === "instagram" ? "instagram" : "facebook"}
          size={12}
          class="text-text-muted"
        />{/if}
    </span>
    <span class="mt-0.5 block truncate text-xs text-text-muted">
      {[
        kindLabel(asset.kind),
        asset.company_name ?? (asset.active ? t("meta.own_channels") : t("meta.channels.nobody")),
      ].join(" · ")}
    </span>
  </span>
  {@render rowMenu(asset)}
{/snippet}

{#snippet emptyState()}
  <div class="px-6 py-12 text-center">
    {#if data.filters.q || data.filters.kind}
      <p class="text-sm text-text-muted">{t("common.no_results")}</p>
    {:else if data.state === "unlinked"}
      <!-- A queue that is empty is done, and says so (docs/UX.md). -->
      <p class="text-sm text-text">{t("meta.channels.empty_unlinked")}</p>
    {:else if !data.status.connected}
      <TriangleAlert size={24} class="mx-auto mb-3 text-text-muted" aria-hidden="true" />
      <p class="text-sm text-text">{t("meta.setup.connect_title")}</p>
      <p class="mx-auto mt-2 max-w-md text-sm text-text-muted">
        {data.canManage ? t("meta.setup.connect_hint") : t("meta.setup.ask_admin")}
      </p>
      {#if data.canManage}
        <a
          href="/settings/meta"
          class="mt-4 inline-flex rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
          >{t("meta.setup.connect_action")}</a
        >
      {/if}
    {:else}
      <p class="text-sm text-text">{t("meta.channels.empty")}</p>
      <p class="mx-auto mt-2 max-w-md text-sm text-text-muted">{t("meta.channels.empty_hint")}</p>
    {/if}
  </div>
{/snippet}

<DataTable
  rows={data.assets}
  columns={table.columns}
  sort={null}
  widths={table.widths}
  locale={data.locale}
  actions={data.canManage ? rowMenu : undefined}
  actionsWidth={110}
  {mobileRow}
  empty={emptyState}
  onresize={table.onResize}
/>

<Pagination
  total={data.total}
  page={data.paging.page}
  limit={data.paging.limit}
  onsize={table.onPageSize}
/>

<Modal bind:open={linkOpen} title={t("meta.channels.link_title", { name: linking?.name ?? "" })}>
  <!-- clear(): each opening is about another row, so nothing of the last one is kept. -->
  <form
    method="POST"
    action="?/link"
    use:enhance={busy.wrap("link", () => async ({ result, update }) => {
      await update({ reset: false });
      if (result.type === "success") linkOpen = false;
    })}
    class="space-y-4"
  >
    <input type="hidden" name="asset_id" value={linking?.id ?? ""} />
    <p class="text-sm text-text-muted">
      {linking?.kind === "ad_account"
        ? t("meta.channels.link_hint_ads")
        : t("meta.channels.link_hint")}
    </p>
    <div>
      <label for="meta-channel-company" class="mb-1 block text-sm font-medium text-text">
        {t("meta.new.client")}
      </label>
      {#if own}
        <input type="hidden" name="company_id" value="" />
        <p class="rounded-lg bg-surface px-3 py-2 text-sm text-text">{t("meta.own_channels")}</p>
      {:else}
        <Combobox
          items={companyPicker.live}
          archived={companyPicker.retired}
          archivedLabel={companyArchivedLabel()}
          name="company_id"
          id="meta-channel-company"
          bind:value={chosen}
          allowEmpty={false}
          placeholder={t("meta.new.client_placeholder")}
          onselect={() => (suggested = false)}
          oncreate={(query) => {
            quickCreateName = query;
            quickCreateOpen = true;
          }}
        />
        {#if suggested && chosen}
          <p class="mt-1 text-xs text-text-muted">{t("meta.channels.suggested")}</p>
        {/if}
      {/if}
      <label class="mt-2.5 flex items-center gap-2 text-sm text-text">
        <input type="checkbox" bind:checked={own} />
        {t("meta.channels.is_own")}
      </label>
    </div>
    {#if companions.length > 0}
      <fieldset class="border-t border-border pt-4">
        <legend class="sr-only">{t("meta.channels.also")}</legend>
        <p class="mb-2 text-sm font-medium text-text">{t("meta.channels.also")}</p>
        {#each companions as companion (companion.id)}
          <label class="flex items-center gap-2 py-1 text-sm text-text">
            <input type="checkbox" name="also" value={companion.id} bind:group={also} />
            <ChannelMark
              channel={companion.kind === "instagram" ? "instagram" : "facebook"}
              class="text-text-muted"
            />
            <span class="min-w-0 truncate">
              {companion.username ? `@${companion.username}` : companion.name}
            </span>
          </label>
        {/each}
      </fieldset>
    {/if}
    <div class="flex justify-end gap-2 border-t border-border pt-4">
      <Button type="button" variant="secondary" onclick={() => (linkOpen = false)}>
        {t("common.cancel")}
      </Button>
      <Button type="submit" loading={busy.is("link")} disabled={busy.active || (!own && !chosen)}>
        {t("meta.channels.link")}
      </Button>
    </div>
  </form>
</Modal>

<CompanyQuickCreate
  bind:open={quickCreateOpen}
  name={quickCreateName}
  pickerSlot={PICKER_SLOT}
  locale={data.locale}
  error={form && "qcError" in form ? (form.qcError as string) : null}
/>

<ConfirmDialog
  bind:open={unlinkOpen}
  title={t("meta.channels.unlink_title", { name: unlinking?.name ?? "" })}
  message={t("meta.channels.unlink_message")}
  consequences={[t("meta.channels.unlink_keeps"), t("meta.channels.unlink_posts")]}
  action="?/unlink"
  fields={{ asset_id: unlinking?.id ?? "" }}
  confirmLabel={t("meta.channels.unlink")}
/>
