<script lang="ts">
  /**
   * The employees on a record, at a glance: every one of them a {@link PersonChip} — avatar and
   * name — the verantwoordelijke first, in the plain text colour, the rest muted.
   *
   * It used to name the primary and stack the others as bare initials discs, which put two
   * different renderings of a person in one row: the first read as a name, the second as an
   * anonymous badge. A cell that runs out of room now drops *people* (`+2`, named in the
   * tooltip), never the names off the people it keeps.
   */
  import { t } from "$lib/core/i18n";
  import { memberLabel } from "$lib/core/members";
  import PersonChip from "$lib/core/ui/PersonChip.svelte";

  interface Member {
    user_id: string;
    full_name?: string | null;
    email: string | null;
    avatar_url?: string | null;
  }
  interface Assignee {
    user_id: string;
    is_primary: boolean;
  }

  let {
    assignees = [],
    members = [],
    max = 1,
    size = "sm",
  }: {
    /** Primary first, as the API returns it. */
    assignees?: Assignee[];
    members?: Member[];
    /**
     * How many chips fit here. A table cell holds one (the verantwoordelijke) and counts the
     * rest, so a row never grows a second line; a detail header raises it to show the roster.
     */
    max?: number;
    size?: "sm" | "md";
  } = $props();

  const named = $derived(
    assignees.map((a) => {
      const member = members.find((m) => m.user_id === a.user_id);
      // An assignee the member lookup doesn't know — a colleague since removed, or a portal
      // membership, which `/members/lookup` deliberately omits — is named as unknown rather
      // than drawn as a nameless disc: this component's whole point is that people carry names.
      return {
        user_id: a.user_id,
        is_primary: a.is_primary,
        name: member?.full_name ?? null,
        email: member?.email ?? null,
        label: member ? memberLabel(member) : t("assignees.unknown"),
        avatarUrl: member?.avatar_url ?? null,
      };
    }),
  );
  // The verantwoordelijke leads even if the API ever hands them over out of order.
  const ordered = $derived([...named].sort((a, b) => Number(b.is_primary) - Number(a.is_primary)));
  const shown = $derived(ordered.slice(0, max));
  const rest = $derived(ordered.slice(max));
</script>

{#if ordered.length > 0}
  <!-- `flex-nowrap`: wrapping made a table row grow a second line for exactly the case this
       component exists to prevent — a long name filling the line and pushing its own `+2`
       counter under it. The chip truncates instead, and the `+N` is `shrink-0` so it survives.
       Under the table's fixed layout a wrapped cell is the difference between a 55px row and
       a 69px one, on every row at once. -->
  <span class="inline-flex min-w-0 flex-nowrap items-center gap-x-3 gap-y-1 align-middle">
    {#each shown as person (person.user_id)}
      <PersonChip
        name={person.name}
        email={person.email}
        label={person.label}
        avatarUrl={person.avatarUrl}
        {size}
        muted={!person.is_primary}
        title={person.is_primary ? `${person.label} · ${t("assignees.primary")}` : person.label}
      />
    {/each}
    {#if rest.length > 0}
      <span class="shrink-0 text-text-muted" title={rest.map((p) => p.label).join(", ")}
        >+{rest.length}</span
      >
    {/if}
  </span>
{/if}
