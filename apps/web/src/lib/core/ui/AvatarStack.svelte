<script lang="ts">
  /**
   * The employees on a record, at a glance: the verantwoordelijke named in full, the rest as a
   * stack of overlapping initials. Beyond `max` the stack ends in a "+n" disc.
   *
   * Same initials rule as {@link TaskRow} and the profile menu, so one person reads the same
   * everywhere.
   */
  import { t } from "$lib/core/i18n";
  import Avatar from "$lib/core/ui/Avatar.svelte";

  interface Member {
    user_id: string;
    full_name?: string | null;
    email: string;
    avatar_url?: string | null;
  }
  interface Assignee {
    user_id: string;
    is_primary: boolean;
  }

  let {
    assignees = [],
    members = [],
    max = 5,
  }: {
    /** Primary first, as the API returns it. */
    assignees?: Assignee[];
    members?: Member[];
    max?: number;
  } = $props();

  const named = $derived(
    assignees.map((a) => {
      const member = members.find((m) => m.user_id === a.user_id);
      return {
        user_id: a.user_id,
        is_primary: a.is_primary,
        name: member ? member.full_name || member.email : a.user_id,
        avatarUrl: member?.avatar_url ?? null,
      };
    }),
  );
  const primary = $derived(named.find((a) => a.is_primary) ?? null);
  const others = $derived(named.filter((a) => !a.is_primary));
  const shown = $derived(others.slice(0, max));
  const overflow = $derived(others.length - shown.length);

</script>

{#if named.length > 0}
  <span class="inline-flex items-center gap-2 align-middle">
    {#if primary}
      <span class="text-text" title={t("assignees.primary")}>{primary.name}</span>
    {/if}
    {#if others.length > 0}
      <span class="flex -space-x-1.5">
        {#each shown as person (person.user_id)}
          <Avatar name={person.name} avatarUrl={person.avatarUrl} size="sm" ring />
        {/each}
        {#if overflow > 0}
          <span
            class="inline-flex h-6 w-6 items-center justify-center rounded-full bg-surface text-[10px] font-medium text-text-muted ring-2 ring-surface-raised"
            title={others
              .slice(max)
              .map((p) => p.name)
              .join(", ")}>+{overflow}</span
          >
        {/if}
      </span>
    {/if}
  </span>
{/if}
