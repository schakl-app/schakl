<script lang="ts">
  /**
   * What a post will roughly look like on one channel, drawn from the form as it stands — so
   * the words are read in the shape a follower reads them in, before anybody schedules them.
   *
   * **An approximation, and it says so.** It draws what decides whether a post *works*: where
   * the channel cuts the text ("… meer"), how several pictures are laid out, that Instagram
   * shows no link. It does not imitate either network's interface, and it uses the app's own
   * colours rather than theirs: a preview dressed as the real thing invites trusting it to the
   * pixel.
   */
  import Clapperboard from "@lucide/svelte/icons/clapperboard";

  import { t } from "$lib/core/i18n";
  import Avatar from "$lib/core/ui/Avatar.svelte";

  import ChannelMark from "./ChannelMark.svelte";

  export interface PreviewMedia {
    kind: string;
    src: string | null;
    alt: string;
  }

  let {
    channel,
    name,
    username = null,
    pictureUrl = null,
    body,
    link = null,
    media = [],
    format = "post",
    when = null,
  }: {
    channel: string;
    name: string;
    /** The time under the name: when it went out, or when it will. Absent is "just now",
     *  which is what a post looks like the moment it is published. */
    when?: string | null;
    username?: string | null;
    pictureUrl?: string | null;
    body: string;
    link?: string | null;
    media?: PreviewMedia[];
    format?: string;
  } = $props();

  const instagram = $derived(channel === "instagram");
  // Where each channel folds the text behind "meer". Instagram cuts a caption at about 125
  // characters; Facebook gives a post roughly five lines before it does.
  const fold = $derived(instagram ? 125 : 480);
  let expanded = $state(false);
  const text = $derived(body.trim());
  const folded = $derived(!expanded && text.length > fold);
  const shown = $derived(folded ? `${text.slice(0, fold).trimEnd()}…` : text);
  const host = $derived.by(() => {
    if (!link) return "";
    try {
      return new URL(link).host.replace(/^www\./, "");
    } catch {
      return link;
    }
  });
  const tiles = $derived(media.slice(0, instagram ? 1 : 4));
  const more = $derived(instagram ? 0 : Math.max(0, media.length - tiles.length));
</script>

<article
  class="overflow-hidden rounded-xl border border-border bg-surface-raised text-sm text-text"
  aria-label={t("meta.preview.label", { channel: instagram ? "Instagram" : "Facebook" })}
>
  <header class="flex items-center gap-2.5 px-3.5 py-3">
    <Avatar {name} avatarUrl={pictureUrl} size="md" />
    <div class="min-w-0 flex-1">
      <p class="truncate font-medium leading-tight">{instagram && username ? username : name}</p>
      <p class="truncate text-xs tabular-nums text-text-muted">{when ?? t("meta.preview.now")}</p>
    </div>
    <ChannelMark {channel} class="text-text-muted" />
  </header>

  {#snippet words()}
    {#if shown}
      <p class="whitespace-pre-wrap break-words px-3.5 pb-3 leading-snug">
        {#if instagram}<span class="mr-1 font-medium">{username ?? name}</span>{/if}{shown}
        {#if folded}
          <button
            type="button"
            class="text-text-muted hover:underline"
            onclick={() => (expanded = true)}>{t("meta.preview.more")}</button
          >
        {/if}
      </p>
    {:else}
      <p class="px-3.5 pb-3 text-text-muted">{t("meta.preview.no_text")}</p>
    {/if}
  {/snippet}

  {#snippet pictures()}
    {#if tiles.length > 0}
      <div
        class="grid gap-0.5 bg-border {tiles.length === 1
          ? 'grid-cols-1'
          : 'grid-cols-2'} {instagram || format === 'reel' ? '' : 'max-h-96'}"
      >
        {#each tiles as tile, index (index)}
          <div
            class="relative flex items-center justify-center overflow-hidden bg-surface
              {format === 'reel' ? 'aspect-[9/16] max-h-96' : instagram ? 'aspect-square' : ''}
              {!instagram && tiles.length === 3 && index === 0 ? 'col-span-2' : ''}"
          >
            {#if tile.kind === "video" || !tile.src}
              <div class="flex aspect-video w-full flex-col items-center justify-center gap-1.5">
                <Clapperboard size={28} class="text-text-muted" aria-hidden="true" />
                <span class="text-xs text-text-muted">{t("meta.preview.video")}</span>
              </div>
            {:else}
              <img
                src={tile.src}
                alt={tile.alt}
                class="h-full w-full {instagram || tiles.length > 1
                  ? 'object-cover'
                  : 'object-contain'}"
                loading="lazy"
              />
            {/if}
            {#if more > 0 && index === tiles.length - 1}
              <span
                class="absolute inset-0 flex items-center justify-center bg-black/50 text-xl font-semibold text-white"
                >+{more}</span
              >
            {/if}
          </div>
        {/each}
      </div>
      {#if instagram && media.length > 1}
        <p class="px-3.5 pt-2 text-xs text-text-muted">
          {t("meta.preview.carousel", { count: String(media.length) })}
        </p>
      {/if}
    {:else if instagram}
      <div class="flex aspect-[2/1] items-center justify-center bg-surface px-6 text-center">
        <span class="text-xs text-text-muted">{t("meta.preview.needs_media")}</span>
      </div>
    {/if}
  {/snippet}

  {#if instagram}
    {@render pictures()}
    <div class="pt-3">{@render words()}</div>
  {:else}
    {@render words()}
    {@render pictures()}
    {#if link && tiles.length === 0}
      <div class="border-t border-border bg-surface px-3.5 py-2.5">
        <p class="truncate text-xs uppercase text-text-muted">{host}</p>
        <p class="truncate text-xs text-text-muted">{t("meta.preview.link_card")}</p>
      </div>
    {/if}
  {/if}
</article>
