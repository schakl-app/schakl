<script lang="ts">
  /**
   * The discussion on a task (#312, and the pass that made it survive fifty comments).
   *
   * It rendered every thread, every answer and every timestamp in one flat column, oldest-first,
   * with no count, no fold and nothing said about the API's 200-row cap. On the three-comment
   * task it was built for that reads fine; on the ones an agency actually talks on it is a wall,
   * and every notification about it landed at the *top of the task* and left the reader to find
   * the words the sentence was about. Five rules came out of fixing that, and they are what this
   * component is:
   *
   * **Order is a preference, and only for threads.** An answer must follow its question or the
   * conversation stops being one, so replies are always oldest-first and what the control flips
   * is the order of the *openers* (`comment-prefs.ts`, which argues the newest-first default).
   *
   * **A long list folds from the far end, and the fold says how much it is hiding.** The most
   * recent conversations stay open — whichever way round they are drawn, so in oldest-first the
   * fold sits above them and in newest-first below. "Toon 28 oudere reacties" is a fold; a list
   * that just stops is silent truncation, which reads as "that is all of them" (docs/UX.md,
   * Principle 7).
   *
   * **A deep link expands before it scrolls.** `?comment=<id>` is what a notification, a mail
   * button and the activity trail all point at now, and a marked comment inside a folded block
   * is a link that appears to do nothing — so the fold, the thread's own reply fold and the
   * reply box are all resolved from the target *before* the browser is asked to scroll to it.
   * Landing there also opens the reply composer, because "someone answered you" and "you are
   * about to answer" are one motion and it was three clicks.
   *
   * **What was written is one bubble.** Opener and answer differ in where they sit and how loud
   * they are, never in what they can do — one snippet, so the ⋯ menu, the edit form and the
   * impersonation badge cannot drift apart.
   *
   * **A face is faster to scan than a name.** The roster is already on the page for the assignee
   * picker, so the author's avatar costs nothing and is what makes "who is talking" answerable
   * without reading. A relative stamp does the same for "when" and keeps the exact one in its
   * `title`.
   */
  import { ChevronDown, Pencil, Reply, Trash2 } from "@lucide/svelte";
  import { tick } from "svelte";

  import { enhance } from "$app/forms";
  import { fmtDateTime, fmtRelativeTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import type { InFlight } from "$lib/core/submit.svelte";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Avatar from "$lib/core/ui/Avatar.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Markdown from "$lib/core/ui/Markdown.svelte";
  import RichTextEditor from "$lib/core/ui/RichTextEditor.svelte";
  import type { CandidateScope } from "$lib/core/richtext/candidates";
  import { DEFAULT_COMMENT_SORT, type CommentSort } from "$lib/modules/tasks/comment-prefs";

  import type { components } from "$lib/core/api/schema";

  type Comment = components["schemas"]["CommentRead"];
  type Member = components["schemas"]["MemberLookup"];

  let {
    comments,
    truncated = false,
    members = [],
    userId,
    canComment,
    canDeleteAny,
    scope,
    busy,
    askDelete,
    sort = DEFAULT_COMMENT_SORT,
    focusId = null,
    onsort,
  }: {
    comments: Comment[];
    /** The read hit its cap: older conversations exist and are not on this page. */
    truncated?: boolean;
    /** The org roster the page already loaded — for the author's face, nothing else. */
    members?: Member[];
    userId: string;
    canComment: boolean;
    /** `tasks.comment.write:any` — cleaning up somebody else's words. */
    canDeleteAny: boolean;
    scope: CandidateScope;
    busy: InFlight;
    askDelete: (action: string, fields: Record<string, string>, message: string) => void;
    sort?: CommentSort;
    /** The comment a notification, a mail or the trail sent this reader to (`?comment=`). */
    focusId?: string | null;
    onsort: (next: CommentSort) => void;
  } = $props();

  /** Threads kept open by default. Above this the older ones fold into one line that counts them. */
  const VISIBLE_THREADS = 5;
  /** Answers kept open inside one thread; the earlier ones fold the same way. */
  const VISIBLE_REPLIES = 3;

  /**
   * Who a comment is attributed to (issue #64). A name with no live account is someone who has
   * since been deleted — say so, rather than rendering the bare “—” this used to.
   */
  function authorLabel(c: Comment): string {
    if (!c.author_name) return "—";
    return c.author_deleted ? t("common.deleted_user", { name: c.author_name }) : c.author_name;
  }

  // --- threading ---------------------------------------------------------------------------- //
  // The API hands back one flat, chronological list carrying `parent_id`; nesting is a display
  // concern, so it is built here. Threads are one level deep by construction (the service
  // re-roots a reply-to-a-reply), which is what lets this be a group-by rather than a recursive
  // component — and what keeps a conversation readable at one indent on a phone.
  type Thread = { root: Comment; replies: Comment[] };
  const threads = $derived.by(() => {
    const out: Thread[] = [];
    // uuid keys, so a plain record indexes them safely — and stays outside Svelte's reactivity
    // rules, which a Map/Set built inside a $derived would trip for no benefit.
    const at: Record<string, number> = Object.create(null);
    const present: Record<string, true> = Object.create(null);
    for (const c of comments) present[c.id] = true;
    for (const c of comments) {
      // A reply whose parent fell outside the response cap opens its own thread rather than
      // vanishing: the read orders by thread so this is rare, but "nothing is shown" is never
      // the better answer to "the conversation is longer than 200 messages".
      const parent = c.parent_id && present[c.parent_id] ? c.parent_id : null;
      const idx = parent === null ? undefined : at[parent];
      if (idx === undefined) {
        at[c.id] = out.length;
        out.push({ root: c, replies: [] });
      } else {
        out[idx].replies.push(c);
      }
    }
    return out;
  });

  /** Which thread a given comment id sits in — what a deep link has to resolve before it scrolls. */
  const threadOf = $derived.by(() => {
    const index: Record<string, string> = Object.create(null);
    for (const thread of threads) {
      index[thread.root.id] = thread.root.id;
      for (const reply of thread.replies) index[reply.id] = thread.root.id;
    }
    return index;
  });

  const total = $derived(comments.length);

  // The API returns threads oldest-first. Only the openers are re-ordered; `replies` are left
  // exactly as they arrived, which is the rule this whole preference is bounded by.
  const ordered = $derived(sort === "newest" ? [...threads].reverse() : threads);

  // --- folding ------------------------------------------------------------------------------ //
  // Always the *most recent* threads that stay open, whichever direction they are drawn in — so
  // the fold is above them reading oldest-first and below them reading newest-first, and either
  // way the news is on screen.
  let showAllThreads = $state(false);
  const hiddenThreads = $derived(
    showAllThreads ? 0 : Math.max(0, threads.length - VISIBLE_THREADS),
  );
  const openThreads = $derived(
    hiddenThreads === 0
      ? ordered
      : sort === "newest"
        ? ordered.slice(0, VISIBLE_THREADS)
        : ordered.slice(hiddenThreads),
  );

  /** Threads whose earlier answers the reader has unfolded, by root id. */
  let repliesShown = $state<Record<string, true>>({});
  function visibleReplies(thread: Thread): Comment[] {
    if (repliesShown[thread.root.id] || thread.replies.length <= VISIBLE_REPLIES) {
      return thread.replies;
    }
    return thread.replies.slice(thread.replies.length - VISIBLE_REPLIES);
  }
  const hiddenReplies = (thread: Thread): number =>
    repliesShown[thread.root.id] ? 0 : Math.max(0, thread.replies.length - VISIBLE_REPLIES);

  // --- replying ----------------------------------------------------------------------------- //
  /** Which thread's reply composer is open, by root id — one at a time, like the edit form. */
  let replyingTo = $state<string | null>(null);
  /** Seeded body for that composer: answering a *reply* addresses its author by name, so the
   *  thread still says who is being answered once several people are in it. */
  let replySeed = $state("");
  /** Remounts the composer, so opening it on another thread never inherits a stale draft. */
  let replyKey = $state(0);
  /** The composer's own element, so arriving from a notification can put the caret in it. */
  let replyBox: HTMLElement | undefined = $state();

  function seedFor(rootId: string, answering: Comment): string {
    // The mention marker is the editor's own syntax (`core/richtext/editor.ts`), so the seed
    // round-trips into a real mention chip rather than literal text. Only for someone with a
    // live account — a departed author has no id to mention.
    return answering.id === rootId || !answering.author_user_id || !answering.author_name
      ? ""
      : `@[${answering.author_name}](mention:${answering.author_user_id}) `;
  }

  function openReply(rootId: string, answering: Comment) {
    replySeed = seedFor(rootId, answering);
    replyingTo = rootId;
    replyKey += 1;
  }

  let editingId = $state<string | null>(null);
  /** Bumped after a comment is posted to remount (and so clear) the markdown editor. */
  let composerKey = $state(0);

  // --- the comment this reader was sent to --------------------------------------------------- //
  // Set from `?comment=` on arrival and again from a comment this reader just posted, so "here
  // are the words" and "here is what you just wrote" are one mechanism. It is deliberately
  // `$state` seeded by an effect rather than a `$derived` of the URL: the mark is dismissible,
  // and a derived one would come back the moment anything else on the page re-rendered.
  let marked = $state<string | null>(null);
  /** The last id we acted on, so re-running the effect does not fight the reader's own clicks. */
  let handled = $state<string | null>(null);
  /** `?comment=` named something this page does not have — said out loud, never swallowed. */
  let missing = $state(false);

  $effect(() => {
    const target = focusId;
    if (!target || target === handled) return;
    handled = target;
    const root = threadOf[target];
    // A comment the page does not hold: over the cap, or deleted since the notification was
    // written. Saying nothing would read as a broken link, so the strip below says so instead.
    if (!root) {
      marked = null;
      missing = true;
      return;
    }
    missing = false;
    marked = target;
    // Expand *everything* that could be hiding it before asking the browser to scroll, or the
    // link silently does nothing (the fold, and the thread's own earlier answers).
    showAllThreads = true;
    repliesShown = { ...repliesShown, [root]: true };
    // Answering is the next thing this reader is here to do, so the box is already open.
    if (canComment) {
      const comment = comments.find((c) => c.id === target);
      replySeed = comment ? seedFor(root, comment) : "";
      replyingTo = root;
      replyKey += 1;
    }
    void reveal(target, canComment);
  });

  /**
   * Put the message on screen and the caret under it — and keep checking, because arriving here
   * is a *navigation* and a navigation moves the scroll position after we do.
   *
   * Three things run after this effect and each one can undo it: Svelte renders the expansions
   * above (so the target's position is not final when the effect body ends), SvelteKit resets
   * focus to `<body>` when the navigation settles (the accessibility rule `clearAndFocus`
   * already works around), and the markdown editor loads asynchronously and grows the page under
   * whatever is already scrolled. One `requestAnimationFrame` lost to all three, silently, which
   * is exactly what a deep link that "does nothing" looks like.
   *
   * So the reveal is repeated over the second after arrival and stops as soon as the message is
   * actually in view — cheap, and it settles on the first pass in the ordinary case. `preventScroll`
   * on the focus, because two scrolls fight each other and the caret would win.
   */
  async function reveal(target: string, alsoFocusReply = false): Promise<void> {
    for (const wait of [0, 60, 200, 500, 900]) {
      await tick();
      if (wait) await new Promise((resolve) => setTimeout(resolve, wait));
      const el = document.getElementById(`comment-${target}`);
      if (!el) continue;
      const box = el.getBoundingClientRect();
      const onScreen = box.top >= 0 && box.bottom <= window.innerHeight;
      if (!onScreen) el.scrollIntoView({ block: "center", behavior: wait ? "auto" : "smooth" });
      // Re-taken on every pass, not once: SvelteKit's post-navigation `reset_focus()` and the
      // editor's own async mount each hand focus back to `<body>` *after* we took it, and one
      // attempt loses to whichever of them happens to run last.
      const caret = alsoFocusReply
        ? replyBox?.querySelector<HTMLElement>('[contenteditable="true"], textarea')
        : null;
      const held = !alsoFocusReply || (!!caret && document.activeElement === caret);
      if (!held) caret?.focus({ preventScroll: true });
      if (onScreen && held) return;
    }
  }

  function setSort(next: CommentSort) {
    if (next === sort) return;
    onsort(next);
  }
</script>

<div class="mb-3 flex flex-wrap items-center justify-between gap-2">
  <h3 class="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-text-muted">
    {t("tasks.comments.title")}
    <!-- The count is on the heading because "how much is there to read?" is the first question a
         discussion is asked, and a folded list cannot answer it by being looked at. -->
    {#if total > 0}
      <span class="rounded-full bg-surface px-1.5 py-0.5 text-[11px] font-medium tabular-nums">
        {total}
      </span>
    {/if}
  </h3>

  <!-- A personal reading preference, in view (docs/UX.md, Principle 6) — not in Instellingen,
       where nobody would look for it, and not in the URL, which is about what is listed. The
       selected side carries weight and a border as well as its colour: a state told only in the
       brand colour is a state the gold-branded tenant cannot see. -->
  {#if threads.length > 1}
    <div
      class="inline-flex overflow-hidden rounded-lg border border-border text-[11px]"
      role="group"
      aria-label={t("tasks.comments.sort_label")}
    >
      {#each [{ key: "newest" as const, label: t("tasks.comments.sort_newest") }, { key: "oldest" as const, label: t("tasks.comments.sort_oldest") }] as option (option.key)}
        <button
          type="button"
          aria-pressed={sort === option.key}
          onclick={() => setSort(option.key)}
          class="px-2 py-1 {sort === option.key
            ? 'bg-brand/10 font-semibold text-text'
            : 'text-text-muted hover:bg-surface hover:text-text'}"
        >
          {option.label}
        </button>
      {/each}
    </div>
  {/if}
</div>

{#if missing}
  <p class="mb-3 rounded-lg border border-border bg-surface/50 p-2 text-xs text-text-muted">
    {t("tasks.comments.target_missing")}
  </p>
{/if}

<!-- POST /tasks/{id}/comments declares `tasks.comment.write`, and the editor was drawn for
     everyone who could read the task: a role without it typed a comment and lost it to a 403 on
     send. The scope is not consulted — a comment you post is your own, so the API refines
     nothing here (`TaskService.add_comment`). -->
{#if canComment}
  <form
    method="POST"
    action="?/addComment"
    use:enhance={busy.wrap("addComment", () => ({ update, result }) => {
      // Reset the editor by remounting it; its internal state survives a plain form reset.
      if (result.type === "success") {
        composerKey += 1;
        // Mark what was just written. Reading oldest-first it is at the far end of a long list,
        // and "did that send?" is not a question a save should leave open.
        const posted = (result.data as { comment_id?: string } | undefined)?.comment_id;
        if (posted) {
          // `handled` too, or the `?comment=` effect would fight this the next time the URL
          // is read. No unfolding: a comment written just now is in the newest few by
          // definition, and expanding twenty-three old conversations to show it would be an
          // odd thing for pressing Reageren to do.
          handled = posted;
          marked = posted;
          void update({ reset: true }).then(() => reveal(posted));
          return;
        }
      }
      void update({ reset: true });
    })}
    class="mb-4"
  >
    {#key composerKey}
      <RichTextEditor
        name="body"
        rows={2}
        required
        placeholder={t("tasks.comments.placeholder")}
        {scope}
      />
    {/key}
    <div class="mt-2 flex justify-end">
      <Button size="sm" loading={busy.is("addComment")}>{t("tasks.comments.send")}</Button>
    </div>
  </form>
{/if}

<!-- One bubble, rendered for a thread opener and for an answer alike (#312): the two differ in
     where they sit and how loud they are, never in what they can do. Duplicating the markup
     would have been two places to keep the ⋯ menu, the edit form and the impersonation badge in
     step. -->
{#snippet bubble(comment: Comment, rootId: string, replyCount: number, isReply: boolean)}
  <!-- Being the author is half of it: `update_comment` refuses a non-author outright and still
       requires the key from the author. Deleting your own needs the same key; deleting someone
       else's needs it at `:any`. -->
  {@const canEdit = comment.author_user_id === userId && canComment}
  {@const canDelete = canEdit || canDeleteAny}
  {@const author = members.find((m) => m.user_id === comment.author_user_id)}
  <div
    id="comment-{comment.id}"
    class="rounded-lg border p-3 {isReply ? 'bg-surface/30' : 'bg-surface/50'} {marked ===
    comment.id
      ? 'border-brand ring-2 ring-brand/40'
      : 'border-border'}"
  >
    <div class="mb-1 flex items-center justify-between gap-2">
      <span class="flex min-w-0 items-center gap-1.5 text-xs font-semibold text-text">
        <Avatar
          size={isReply ? "xs" : "sm"}
          name={comment.author_name}
          email={author?.email}
          avatarUrl={author?.avatar_url}
        />
        <span class="truncate">{authorLabel(comment)}</span>
        <!-- Written through this account by someone else (#296): the agency's own words would
             otherwise sit under the client's name with nothing to say so. -->
        {#if comment.impersonator_name}
          <span
            class="rounded bg-amber-100 px-1.5 py-0.5 text-[11px] font-medium text-amber-900 dark:bg-amber-950 dark:text-amber-300"
            title={t("activity.impersonated_title", { actor: comment.impersonator_name })}
          >
            {t("activity.via_impersonator", { actor: comment.impersonator_name })}
          </span>
        {/if}
      </span>
      <div class="flex shrink-0 items-center gap-1 text-[11px] text-text-muted">
        <span title={fmtDateTime(comment.created_at)}>{fmtRelativeTime(comment.created_at)}</span>
        {#if comment.edited_at}<span>({t("tasks.comments.edited")})</span>{/if}
        {#if canDelete}
          <ActionsMenu
            compact
            items={[
              ...(canEdit
                ? [
                    {
                      label: t("common.edit"),
                      icon: Pencil,
                      onclick: () => (editingId = editingId === comment.id ? null : comment.id),
                    },
                  ]
                : []),
              {
                label: t("common.delete"),
                icon: Trash2,
                danger: true,
                // Deleting a thread opener takes its answers with it (ON DELETE CASCADE), so the
                // confirm counts them: a dialog that says "this comment" while five messages
                // disappear is the one thing an undo-less delete may not do.
                onclick: () =>
                  askDelete(
                    "?/deleteComment",
                    { comment_id: comment.id },
                    replyCount === 0
                      ? t("tasks.comments.delete_confirm")
                      : replyCount === 1
                        ? t("tasks.comments.delete_thread_confirm_one")
                        : t("tasks.comments.delete_thread_confirm_other", { count: replyCount }),
                  ),
              },
            ]}
          />
        {/if}
      </div>
    </div>
    {#if editingId === comment.id}
      <form
        method="POST"
        action="?/editComment"
        use:enhance={busy.wrap("editComment", () => ({ update }) => {
          editingId = null;
          void update({ reset: false });
        })}
      >
        <input type="hidden" name="comment_id" value={comment.id} />
        <RichTextEditor name="body" rows={2} required value={comment.body} {scope} />
        <div class="mt-1 flex gap-2">
          <Button size="xs" loading={busy.is("editComment")}>{t("common.save")}</Button>
          <button
            type="button"
            class="rounded-lg border border-border px-2 py-1 text-xs"
            onclick={() => (editingId = null)}>{t("common.cancel")}</button
          >
        </div>
      </form>
    {:else}
      <Markdown value={comment.body} />
      <!-- Answering is not "editing the definition", so it stays inline rather than hiding in
           the ⋯ menu (docs/UX.md). It gates on the same permission the POST declares — a client
           portal login holds it, and its own task comments are its whole write surface. -->
      {#if canComment}
        <button
          type="button"
          class="mt-1.5 inline-flex items-center gap-1 rounded text-[11px] font-medium text-text-muted hover:text-text"
          onclick={() => openReply(rootId, comment)}
        >
          <Reply class="size-3" aria-hidden="true" />
          {t("tasks.comments.reply")}
        </button>
      {/if}
    {/if}
  </div>
{/snippet}

{#snippet threadItem(thread: Thread)}
  <li>
    {@render bubble(thread.root, thread.root.id, thread.replies.length, false)}

    <!-- Answers hang off their opener under one rule, at one indent. A second level would indent
         itself off a phone; the API re-roots instead of nesting deeper. -->
    {#if thread.replies.length > 0}
      <ul class="mt-2 space-y-2 border-l-2 border-border pl-3 sm:pl-4">
        {#if hiddenReplies(thread) > 0}
          <li>
            <button
              type="button"
              class="inline-flex items-center gap-1 text-[11px] font-medium text-text-muted underline-offset-2 hover:text-text hover:underline"
              onclick={() => (repliesShown = { ...repliesShown, [thread.root.id]: true })}
            >
              <ChevronDown class="size-3" aria-hidden="true" />
              {hiddenReplies(thread) === 1
                ? t("tasks.comments.show_earlier_replies_one")
                : t("tasks.comments.show_earlier_replies_other", {
                    count: hiddenReplies(thread),
                  })}
            </button>
          </li>
        {/if}
        {#each visibleReplies(thread) as reply (reply.id)}
          <li>{@render bubble(reply, thread.root.id, 0, true)}</li>
        {/each}
      </ul>
    {/if}

    {#if replyingTo === thread.root.id}
      <form
        method="POST"
        action="?/addComment"
        bind:this={replyBox}
        use:enhance={busy.wrap("addComment", () => ({ update, result }) => {
          // Close on success, keep the draft on failure — the words are not the server's to
          // throw away (docs/UX.md, the reset rule).
          if (result.type === "success") {
            replyingTo = null;
            const posted = (result.data as { comment_id?: string } | undefined)?.comment_id;
            if (posted) {
              handled = posted;
              marked = posted;
              repliesShown = { ...repliesShown, [thread.root.id]: true };
            }
          }
          void update({ reset: result.type === "success" });
        })}
        class="mt-2 border-l-2 border-brand/40 pl-3 sm:pl-4"
      >
        <input type="hidden" name="parent_id" value={thread.root.id} />
        <p class="mb-1 text-[11px] text-text-muted">
          {t("tasks.comments.reply_to", { name: authorLabel(thread.root) })}
        </p>
        {#key replyKey}
          <RichTextEditor
            name="body"
            rows={2}
            required
            value={replySeed}
            placeholder={t("tasks.comments.reply_placeholder")}
            {scope}
          />
        {/key}
        <div class="mt-2 flex gap-2">
          <Button size="xs" loading={busy.is("addComment")}>{t("tasks.comments.send")}</Button>
          <button
            type="button"
            class="rounded-lg border border-border px-2 py-1 text-xs"
            onclick={() => (replyingTo = null)}>{t("common.cancel")}</button
          >
        </div>
      </form>
    {/if}
  </li>
{/snippet}

<!-- The fold. One line, and it names the number it is hiding — a list that simply stops looks
     exactly like a list that is complete. -->
{#snippet fold()}
  <button
    type="button"
    class="flex w-full items-center justify-center gap-1.5 rounded-lg border border-dashed border-border py-2 text-xs font-medium text-text-muted hover:border-brand/50 hover:text-text"
    onclick={() => (showAllThreads = true)}
  >
    <ChevronDown class="size-3.5" aria-hidden="true" />
    {hiddenThreads === 1
      ? t("tasks.comments.show_older_one")
      : t("tasks.comments.show_older_other", { count: hiddenThreads })}
  </button>
{/snippet}

<!-- Older conversations exist and are not on this page (the API caps the read). It sits at the
     old end, whichever end that is, because that is where the reader runs out. -->
{#snippet capNotice()}
  <p class="text-[11px] text-text-muted">{t("tasks.comments.capped")}</p>
{/snippet}

{#if threads.length === 0}
  <p class="text-sm text-text-muted">{t("tasks.comments.empty")}</p>
{:else}
  <div class="space-y-3">
    <!-- The fold and the cap notice sit at the *old* end of the list, which swaps with the
         order — reading oldest-first that is the top, reading newest-first the bottom. The
         thread list itself is one `{#each}` either way: `openThreads` is already in reading
         order, and expanding simply makes it the whole set. -->
    {#if sort === "oldest"}
      {#if truncated && showAllThreads}{@render capNotice()}{/if}
      {#if hiddenThreads > 0}{@render fold()}{/if}
    {/if}
    <ul class="space-y-3">
      {#each openThreads as thread (thread.root.id)}{@render threadItem(thread)}{/each}
    </ul>
    {#if sort === "newest"}
      {#if hiddenThreads > 0}{@render fold()}{/if}
      {#if truncated && showAllThreads}{@render capNotice()}{/if}
    {/if}
  </div>
{/if}
