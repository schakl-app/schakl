<script lang="ts">
  /**
   * One post's page. The composer is keyed on the post, so stepping from one post to another —
   * a copy lands on its own page by redirect — starts from that post's words rather than
   * carrying the last one's form along.
   */
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import PostComposer from "$lib/integrations/meta/PostComposer.svelte";

  let { data, form } = $props();
</script>

<svelte:head>
  <title>{pageTitle(data.post.title || t("meta.composer.untitled"))}</title>
</svelte:head>

{#key data.post.id}
  <PostComposer
    post={data.post}
    channels={data.channels}
    trail={data.trail}
    canWrite={data.canWrite}
    canPublish={data.canPublish}
    scheduler={data.status.facebook_scheduler}
    {form}
  />
{/key}
