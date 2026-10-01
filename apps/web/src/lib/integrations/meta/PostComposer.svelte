<script lang="ts">
  /**
   * One post, on one page: written on the left, read on the right.
   *
   * **The page has one form and every button posts it.** "Inplannen" schedules what is on the
   * screen, not what was last saved; attaching a picture saves the paragraph above it first.
   * One gesture is one save (docs/UX.md), and nothing on this page can lose words by being
   * pressed in the wrong order.
   *
   * **A draft saves itself**, a moment after the typing stops: the checks on the right are the
   * API's own (what a channel will accept is decided there, once), so they are only as fresh
   * as the last save, and a list of problems that describes the text as it was five minutes
   * ago is worse than none. A **scheduled** post does not — changing what will be published
   * under a client's name is a decision, and it gets a button.
   *
   * **What may be pressed is what the API would allow.** Every control mirrors the key its
   * call makes (`meta.post.write` for words, `meta.post.publish` for anything a follower
   * reads), never a guess about who the viewer is (#310).
   */
  import type { ComponentProps } from "svelte";

  import CopyPlus from "@lucide/svelte/icons/copy-plus";
  import ExternalLink from "@lucide/svelte/icons/external-link";
  import ImagePlus from "@lucide/svelte/icons/image-plus";
  import Send from "@lucide/svelte/icons/send";
  import Trash2 from "@lucide/svelte/icons/trash-2";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import Circle from "@lucide/svelte/icons/circle";
  import Info from "@lucide/svelte/icons/info";
  import Undo2 from "@lucide/svelte/icons/undo-2";
  import X from "@lucide/svelte/icons/x";
  import { onMount, tick, untrack } from "svelte";

  import { enhance } from "$app/forms";
  import { beforeNavigate, invalidate } from "$app/navigation";
  import ActivityFeed from "$lib/core/activity/ActivityFeed.svelte";
  import { pastedImageName } from "$lib/core/files/paste";
  import { fmtDateTime, fmtNumber, fmtRelativeTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { pollWhile } from "$lib/core/poll.svelte";
  import { InFlight } from "$lib/core/submit.svelte";
  import ActionsMenu, { type ActionItem } from "$lib/core/ui/ActionsMenu.svelte";
  import Avatar from "$lib/core/ui/Avatar.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Card from "$lib/core/ui/Card.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import { filedrop } from "$lib/core/ui/filedrop";
  import { openLightbox } from "$lib/core/ui/lightbox.svelte";
  import PageHeader from "$lib/core/ui/PageHeader.svelte";
  import StateMark from "$lib/core/ui/StateMark.svelte";
  import TimeInput from "$lib/core/ui/TimeInput.svelte";
  import { localDayTime } from "$lib/core/wallclock";

  import ChannelChips from "./ChannelChips.svelte";
  import ChannelMark from "./ChannelMark.svelte";
  import {
    adviceFor,
    channelLabel,
    countHashtags,
    errorText,
    inFlight,
    issueText,
    LIMITS,
    statusState,
    targetStatusLabel,
  } from "./format";
  import PostPreview from "./PostPreview.svelte";
  import PostStatusPill from "./PostStatusPill.svelte";
  import type { Channel, ChannelOption, SocialPost } from "./types";

  interface Outcome {
    done?: string;
    error?: string;
    fields?: Record<string, string>;
    fileError?: string;
    verb?: string;
  }

  let {
    post,
    channels,
    trail,
    canWrite,
    canPublish,
    scheduler = "schakl",
    form = null,
  }: {
    post: SocialPost;
    /** Every channel a post could go to; the composer offers this post's client's. */
    channels: ChannelOption[];
    trail: ComponentProps<typeof ActivityFeed>["items"];
    canWrite: boolean;
    canPublish: boolean;
    /** Who publishes a scheduled Facebook post — said beside the time, because it decides
     *  whether the post shows up in Meta Business Suite before it is live. */
    scheduler?: string;
    form?: Outcome | null;
  } = $props();

  const busy = new InFlight();

  // --- what may be done ----------------------------------------------------------------------
  const status = $derived(post.status);
  const open = $derived(status === "draft" || status === "review" || status === "scheduled");
  const mayEdit = $derived(open && (status === "scheduled" ? canPublish : canWrite));
  // A draft and a post in review save themselves; a scheduled one is changed on purpose.
  const autosaves = $derived(mayEdit && status !== "scheduled");

  // --- the form, as state ---------------------------------------------------------------------
  // Initialised once from the post and then the page's own: a reload after a save must never
  // put the server's copy back over a sentence that is being typed. The host re-keys this
  // component per post, so "once" is once per post.
  const first = untrack(() => post);
  const clock = first.scheduled_at ? localDayTime(first.scheduled_at) : { day: "", time: "09:00" };

  let body = $state(first.body);
  let link = $state(first.link ?? "");
  let notes = $state(first.notes);
  let format = $state<string>(first.format);
  let day = $state(clock.day);
  let time = $state(clock.time);
  let selected = $state(first.targets.map((target) => target.asset_id));
  let overrides = $state<Record<string, string>>(
    Object.fromEntries(
      first.targets
        .filter((target) => target.body_override)
        .map((target) => [target.asset_id, target.body_override ?? ""]),
    ),
  );
  let media = $state(
    first.media.map((item) => ({
      kind: item.kind,
      file_id: item.file_id ?? null,
      url: item.url ?? null,
      alt: item.alt ?? "",
      width: item.width ?? null,
      height: item.height ?? null,
    })),
  );
  let videoUrl = $state("");

  // The server owns which files a post has: after an upload its list is longer than ours, and
  // ours takes the new tiles on without losing an alt text typed a moment ago.
  $effect(() => {
    const server = post.media;
    untrack(() => {
      const known = new Set(media.map((item) => item.file_id ?? item.url));
      const fresh = server.filter((item) => !known.has(item.file_id ?? item.url ?? ""));
      const measured = new Map(server.map((item) => [item.file_id ?? item.url, item]));
      if (fresh.length > 0) {
        media = [
          ...media,
          ...fresh.map((item) => ({
            kind: item.kind,
            file_id: item.file_id ?? null,
            url: item.url ?? null,
            alt: item.alt ?? "",
            width: item.width ?? null,
            height: item.height ?? null,
          })),
        ];
      }
      media = media.map((item) => {
        const seen = measured.get(item.file_id ?? item.url);
        return seen ? { ...item, width: seen.width ?? null, height: seen.height ?? null } : item;
      });
    });
  });

  // This post's client's channels, plus any the post already names (a channel switched off
  // since is still on the post, and must be drawn to be taken off it).
  const offered = $derived(
    channels.filter(
      (channel) =>
        (channel.companyId ?? null) === (post.company_id ?? null) || selected.includes(channel.id),
    ),
  );
  const chosen = $derived(offered.filter((channel) => selected.includes(channel.id)));
  const networks = $derived([...new Set(chosen.map((channel) => channel.channel))] as Channel[]);

  const serialised = $derived(
    JSON.stringify({
      body,
      link,
      notes,
      format,
      day,
      time,
      selected,
      overrides,
      media: media.map((item) => [item.file_id ?? item.url, item.kind, item.alt]),
    }),
  );
  let saved = $state(untrack(() => serialised));
  const dirty = $derived(serialised !== saved);

  /** Everything the form posts, as hidden fields — so a dialog's own form can carry it too. */
  const fields = $derived<Record<string, string>>(
    mayEdit
      ? {
          editable: "1",
          body,
          link,
          notes,
          format,
          day,
          time,
          asset_ids_json: JSON.stringify(selected),
          overrides_json: JSON.stringify(
            Object.fromEntries(selected.map((id) => [id, overrides[id] ?? ""])),
          ),
          media_json: JSON.stringify(
            media.map((item) => ({
              kind: item.kind,
              ...(item.file_id ? { file_id: item.file_id } : { url: item.url }),
              alt: item.alt,
            })),
          ),
        }
      : {},
  );

  // --- saving --------------------------------------------------------------------------------
  let formEl = $state<HTMLFormElement | null>(null);
  let uploadButton = $state<HTMLButtonElement | null>(null);
  let fileInput = $state<HTMLInputElement | null>(null);
  let sending = "";

  /** Which button was pressed names what is in flight, so only that button spins. */
  const pressed: Parameters<InFlight["wrap"]>[0] = (input) => {
    sending = serialised;
    return input.action.search.replace("?/", "") || "save";
  };

  /**
   * Autosave, straight to the API and never through the form.
   *
   * A form action that succeeds hands focus back to the page (SvelteKit's `reset_focus`), so
   * an autosave built on `requestSubmit` takes the caret out of the box somebody is typing
   * in, a second and a half after every pause. The buttons still post the form: pressing
   * one is a gesture, and where focus lands after a gesture is the page's to decide.
   */
  let autosaving = $state(false);
  let autosaveError = $state<string | null>(null);

  function payload(): Record<string, unknown> {
    return {
      body,
      link: link.trim() || null,
      notes,
      format,
      asset_ids: selected,
      // Naked: the API reads a naive clock on the org's own calendar (CLAUDE.md §8).
      scheduled_at: day ? `${day}T${time || "09:00"}:00` : null,
      media: media.map((item) => ({
        kind: item.kind,
        ...(item.file_id ? { file_id: item.file_id } : { url: item.url }),
        alt: item.alt,
      })),
      overrides: Object.fromEntries(selected.map((id) => [id, overrides[id] ?? ""])),
    };
  }

  async function autosave(options: { keepalive?: boolean } = {}): Promise<void> {
    if (!autosaves || !dirty || busy.active || autosaving || selected.length === 0) return;
    const sent = serialised;
    autosaving = true;
    try {
      const response = await fetch(`/api/v1/meta-business/posts/${post.id}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload()),
        keepalive: options.keepalive ?? false,
      });
      if (response.ok) {
        saved = sent;
        autosaveError = null;
        // The checks and the title are the server's reading of what was just stored.
        await invalidate("meta:post");
      } else {
        const envelope = await response.json().catch(() => null);
        autosaveError = envelope?.error?.message ?? "errors.server";
      }
    } catch {
      autosaveError = "errors.unreachable";
    } finally {
      autosaving = false;
    }
  }

  let timer: ReturnType<typeof setTimeout> | undefined;
  $effect(() => {
    // Read so the effect follows the form; everything it *does* is untracked. It also
    // follows `autosaving`, so words typed while a save was out are saved after it.
    void serialised;
    void autosaving;
    if (!autosaves) return;
    clearTimeout(timer);
    timer = setTimeout(() => untrack(() => void autosave()), 1500);
    return () => clearTimeout(timer);
  });

  // Leaving must not lose the last sentence: a navigation saves first, and the browser's
  // own exits get a request the tab may close on.
  beforeNavigate(() => {
    if (dirty) void autosave({ keepalive: true });
  });
  onMount(() => {
    const leaving = () => {
      if (dirty) void autosave({ keepalive: true });
    };
    window.addEventListener("beforeunload", leaving);
    return () => window.removeEventListener("beforeunload", leaving);
  });

  /**
   * The minutes around the planned time. Somebody who plans a post for 09:00 and keeps the
   * page open expects to see it go out: from half a minute before until a quarter of an hour
   * after, the page reads the post again. Outside that window it asks nothing, so a tab left
   * open overnight is not talking to the API in the morning.
   */
  let due = $state(false);
  $effect(() => {
    due = false;
    if (status !== "scheduled" || !post.scheduled_at) return;
    const at = new Date(post.scheduled_at).getTime();
    const opens = at - 30_000 - Date.now();
    const closes = at + 15 * 60_000 - Date.now();
    if (closes <= 0) return;
    // A timer's delay is a 32-bit number of milliseconds: further out than a day, the page
    // will have been reloaded long before it matters.
    if (opens > 86_400_000) return;
    const start = setTimeout(() => (due = true), Math.max(opens, 0));
    const stop = setTimeout(() => (due = false), closes);
    return () => {
      clearTimeout(start);
      clearTimeout(stop);
    };
  });

  // A worker is publishing, or is about to: read the post again until it is done.
  pollWhile(
    () => inFlight(status) || due,
    () => invalidate("meta:post"),
    3000,
  );

  // --- pictures ------------------------------------------------------------------------------
  let localError = $state<string | null>(null);
  const fileError = $derived(localError ?? form?.fileError ?? null);

  async function upload(): Promise<void> {
    localError = null;
    await tick();
    formEl?.requestSubmit(uploadButton);
  }

  function onpaste(event: ClipboardEvent): void {
    if (!mayEdit || format === "reel") return;
    const files = [...(event.clipboardData?.files ?? [])].filter((file) =>
      file.type.startsWith("image/"),
    );
    // Text bound for the box is the box's: only a paste that *is* a picture is an upload.
    if (files.length === 0 || !fileInput) return;
    event.preventDefault();
    const transfer = new DataTransfer();
    for (const file of files) {
      transfer.items.add(new File([file], pastedImageName(file), { type: file.type }));
    }
    fileInput.files = transfer.files;
    void upload();
  }

  function move(index: number, by: number): void {
    const next = [...media];
    const [item] = next.splice(index, 1);
    next.splice(index + by, 0, item);
    media = next;
  }

  function addVideo(): void {
    const url = videoUrl.trim();
    if (!url) return;
    media = format === "reel" ? [{ ...blank(url) }] : [...media, blank(url)];
    videoUrl = "";
  }

  function blank(url: string) {
    return { kind: "video" as const, file_id: null, url, alt: "", width: null, height: null };
  }

  const thumb = (item: { file_id: string | null }, size = 480) =>
    item.file_id ? `/api/v1/files/${item.file_id}/thumbnail?size=${size}` : null;

  const previewMedia = $derived(
    media.map((item) => ({ kind: item.kind, src: thumb(item, 1200), alt: item.alt })),
  );

  // --- what the API said ---------------------------------------------------------------------
  const issues = $derived(post.issues);
  const errors = $derived(issues.filter((issue) => issue.level === "error"));
  const warnings = $derived(issues.filter((issue) => issue.level === "warning"));
  const issueFor = (field: string) => errors.find((issue) => issue.field === field);

  /**
   * Nothing has been written yet. A blank draft is not a mistake: what it still needs is a
   * list of things to do, drawn without alarm, and only once there is something on the page
   * does "this cannot go out" become a verdict worth a red line.
   */
  const unwritten = $derived(!body.trim() && !link.trim() && media.length === 0);

  /**
   * The org's clock, read every half minute: a time that was ahead when it was typed is
   * behind a moment later, and "ready to go out" over a time that has passed is a promise
   * the schedule button then breaks.
   */
  let clockNow = $state(localDayTime(new Date().toISOString()));
  onMount(() => {
    const tick = setInterval(() => (clockNow = localDayTime(new Date().toISOString())), 30_000);
    return () => clearInterval(tick);
  });
  const passed = $derived(
    day !== "" && `${day}T${time || "09:00"}` < `${clockNow.day}T${clockNow.time}`,
  );

  /** "Instagram · Nova Fietsen": the channel first, because two channels share one name. */
  const channelTitle = (channel: ChannelOption) =>
    `${channelLabel(channel.channel)} · ${channel.username ? `@${channel.username}` : channel.name}`;
  const refused = (field: string) => form?.fields?.[field] ?? null;

  const when = $derived(post.published_at ?? post.scheduled_at);
  const title = $derived(post.title || t("meta.composer.untitled"));
  const canSchedule = $derived(canPublish && mayEdit && selected.length > 0);

  // --- dialogs -------------------------------------------------------------------------------
  let confirmingPublish = $state(false);
  let confirmingCancel = $state(false);
  let confirmingDelete = $state(false);
  let dialogError = $state<string | null>(null);
  let duplicateForm = $state<HTMLFormElement | null>(null);

  const menu = $derived.by(() => {
    const items: ActionItem[] = [];
    if (canPublish && open) {
      items.push({
        label: t("meta.action.publish_now"),
        icon: Send,
        onclick: () => (confirmingPublish = true),
      });
    }
    if (canWrite) {
      items.push({
        label: t("meta.action.duplicate"),
        icon: CopyPlus,
        onclick: () => duplicateForm?.requestSubmit(),
      });
    }
    if (open && (status === "scheduled" ? canPublish : canWrite)) {
      items.push({
        label: t("meta.action.cancel"),
        icon: X,
        onclick: () => (confirmingCancel = true),
      });
    }
    if (canWrite && ["draft", "review", "cancelled", "failed"].includes(status)) {
      items.push({
        label: t("common.delete"),
        icon: Trash2,
        danger: true,
        onclick: () => (confirmingDelete = true),
      });
    }
    return items;
  });

  const inputClass =
    "w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand disabled:opacity-60";
</script>

<svelte:window {onpaste} />

<PageHeader {title}>
  {#snippet beside()}<PostStatusPill {status} />{/snippet}
  {#snippet subtitle()}
    {[
      post.company_name ?? t("meta.own_channels"),
      when
        ? t(post.published_at ? "meta.composer.published_at" : "meta.composer.planned_for", {
            when: fmtDateTime(when),
          })
        : "",
    ]
      .filter(Boolean)
      .join(" · ")}
  {/snippet}
  {#snippet actions()}
    {#if autosaves}
      <span class="text-xs text-text-muted" aria-live="polite">
        {#if autosaveError}
          <span class="text-red-600 dark:text-red-400" title={t(autosaveError)}
            >{t("meta.composer.save_failed")}</span
          >
          <button
            type="button"
            class="ml-1 text-brand hover:underline"
            onclick={() => void autosave()}>{t("meta.action.retry")}</button
          >
        {:else if busy.is("save") || autosaving}{t("meta.composer.saving")}
        {:else if dirty}{t("meta.composer.unsaved")}
        {:else}{t("meta.composer.saved")}{/if}
      </span>
    {/if}
    {#if status === "scheduled" && mayEdit}
      <Button
        type="submit"
        form="post-form"
        formaction="?/save"
        variant="secondary"
        loading={busy.is("save")}
        disabled={busy.active || !dirty}
      >
        {t("meta.action.save_changes")}
      </Button>
      <Button
        type="submit"
        form="post-form"
        formaction="?/unschedule"
        variant="secondary"
        loading={busy.is("unschedule")}
        disabled={busy.active}
      >
        <Undo2 size={15} aria-hidden="true" />
        {t("meta.action.unschedule")}
      </Button>
    {:else if status === "review"}
      {#if canWrite}
        <Button
          type="submit"
          form="post-form"
          formaction="?/recall"
          variant="secondary"
          loading={busy.is("recall")}
          disabled={busy.active}
        >
          <Undo2 size={15} aria-hidden="true" />
          {t("meta.action.recall")}
        </Button>
      {/if}
      {#if canPublish}
        <Button
          type="submit"
          form="post-form"
          formaction="?/schedule"
          loading={busy.is("schedule")}
          disabled={busy.active || !canSchedule || !day}
          title={day ? undefined : t("meta.issue.no_time")}
        >
          {t("meta.action.approve")}
        </Button>
      {/if}
    {:else if status === "draft" && mayEdit}
      {#if canPublish}
        <Button
          type="submit"
          form="post-form"
          formaction="?/schedule"
          loading={busy.is("schedule")}
          disabled={busy.active || !canSchedule || !day}
          title={day ? undefined : t("meta.issue.no_time")}
        >
          {t("meta.action.schedule")}
        </Button>
      {:else}
        <Button
          type="submit"
          form="post-form"
          formaction="?/offer"
          loading={busy.is("offer")}
          disabled={busy.active || selected.length === 0}
        >
          {t("meta.action.offer")}
        </Button>
      {/if}
    {:else if (status === "failed" || status === "partial") && canPublish}
      <Button
        type="submit"
        form="post-form"
        formaction="?/retry"
        loading={busy.is("retry")}
        disabled={busy.active}
      >
        {t("meta.action.retry")}
      </Button>
      {#if status === "failed"}
        <!-- Meta turned it down: sending the same words again is one way on, changing them
             is the other, and only the second fixes a refusal about the words. -->
        <Button
          type="submit"
          form="post-form"
          formaction="?/unschedule"
          variant="secondary"
          loading={busy.is("unschedule")}
          disabled={busy.active}
        >
          {t("meta.action.rework")}
        </Button>
      {/if}
    {/if}
    {#if menu.length > 0}<ActionsMenu items={menu} />{/if}
  {/snippet}
</PageHeader>

{#if form?.error || dialogError}
  <p
    class="mb-4 flex items-start gap-2 rounded-lg border border-border bg-surface-raised p-3 text-sm text-text"
    role="alert"
  >
    <TriangleAlert
      size={16}
      class="mt-0.5 shrink-0 text-red-600 dark:text-red-400"
      aria-hidden="true"
    />
    <span>{t(dialogError ?? form?.error ?? "errors.server")}</span>
  </p>
{/if}

{#if status === "review" && !canPublish}
  <p class="mb-4 rounded-lg bg-surface-tint px-4 py-3 text-sm text-text">
    {t("meta.composer.waiting_for_approval")}
  </p>
{/if}

<form
  id="post-form"
  bind:this={formEl}
  method="POST"
  action="?/save"
  enctype="multipart/form-data"
  use:enhance={busy.wrap(pressed, () => async ({ result, update }) => {
    // This page edits something that exists: a reset would blank what was typed.
    await update({ reset: false });
    if (result.type === "success") saved = sending;
  })}
>
  {#each Object.entries(fields) as [name, value] (name)}
    <input type="hidden" {name} {value} />
  {/each}
  <button
    bind:this={uploadButton}
    type="submit"
    formaction="?/upload"
    class="hidden"
    tabindex="-1"
    aria-hidden="true"
  ></button>

  <div class="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,24rem)]">
    <div class="min-w-0 space-y-6">
      {#if !open}
        <!-- What came of it, first: once a post has gone out the page is a record, and the
             question a reader has is "did it land, and where". -->
        <Card title={t("meta.outcome.title")}>
          <ul class="divide-y divide-border">
            {#each post.targets as target (target.id)}
              <li class="flex items-start gap-3 py-3 first:pt-0 last:pb-0">
                <Avatar name={target.asset_name} avatarUrl={target.asset_picture_url} size="md" />
                <div class="min-w-0 flex-1">
                  <p class="flex items-center gap-1.5 text-sm font-medium text-text">
                    <ChannelMark channel={target.channel} size={14} class="text-text-muted" />
                    <span class="truncate">{target.asset_name}</span>
                  </p>
                  <!-- The chip on the right says what became of it; this line says where
                       and when, which the chip cannot. -->
                  <p class="mt-0.5 truncate text-xs tabular-nums text-text-muted">
                    {channelLabel(target.channel)}
                    {#if target.published_at}· {fmtDateTime(target.published_at)}{/if}
                  </p>
                  {#if target.last_error && target.status !== "published"}
                    <p class="mt-1.5 break-words text-sm text-text">
                      {errorText(target.last_error)}
                    </p>
                    {#if adviceFor(target.last_error_code)}
                      <p class="mt-0.5 text-xs text-text-muted">
                        {adviceFor(target.last_error_code)}
                      </p>
                    {/if}
                  {/if}
                </div>
                <div class="flex shrink-0 flex-col items-end gap-1.5">
                  <StateMark
                    state={statusState(target.status)}
                    label={targetStatusLabel(target.status)}
                    variant="chip"
                  />
                  {#if target.permalink}
                    <a
                      href={target.permalink}
                      target="_blank"
                      rel="noopener noreferrer"
                      class="inline-flex items-center gap-1 text-xs text-brand hover:underline"
                    >
                      {t("meta.outcome.open", { channel: channelLabel(target.channel) })}
                      <ExternalLink size={12} aria-hidden="true" />
                    </a>
                  {/if}
                </div>
              </li>
            {/each}
          </ul>
        </Card>
      {/if}

      {#if !open}
        <!-- A record. What went out (or did not) is read, not edited: a form of disabled
             boxes says "you may not" where the page means "this is what it was". -->
        <Card title={t("meta.composer.sent")}>
          {#if body}
            <p class="whitespace-pre-wrap break-words text-sm leading-relaxed text-text">{body}</p>
          {:else}
            <p class="text-sm text-text-muted">{t("meta.preview.no_text")}</p>
          {/if}
          {#each chosen.filter((channel) => overrides[channel.id]) as channel (channel.id)}
            <div class="mt-4 border-t border-border pt-3">
              <p class="mb-1 text-xs font-medium text-text-muted">{channelTitle(channel)}</p>
              <p class="whitespace-pre-wrap break-words text-sm leading-relaxed text-text">
                {overrides[channel.id]}
              </p>
            </div>
          {/each}
          {#if link}
            <p class="mt-3 text-sm">
              <a
                href={link}
                target="_blank"
                rel="noopener noreferrer"
                class="break-all text-brand hover:underline">{link}</a
              >
            </p>
          {/if}
          {#if media.length > 0}
            <ul class="mt-4 grid grid-cols-3 gap-2 sm:grid-cols-4">
              {#each media as item, index (item.file_id ?? item.url)}
                <li class="min-w-0">
                  {#if thumb(item)}
                    <img
                      src={thumb(item)}
                      alt={item.alt || t("meta.composer.image_n", { n: String(index + 1) })}
                      loading="lazy"
                      class="aspect-square w-full rounded-lg border border-border object-cover"
                    />
                  {:else}
                    <span
                      class="flex aspect-square w-full items-center justify-center rounded-lg border border-border bg-surface px-2 text-center text-xs text-text-muted"
                      >{t("meta.preview.video")}</span
                    >
                  {/if}
                </li>
              {/each}
            </ul>
          {/if}
          {#if notes}
            <div class="mt-4 border-t border-border pt-3">
              <p class="mb-1 text-xs font-medium text-text-muted">{t("meta.composer.notes")}</p>
              <p class="whitespace-pre-wrap break-words text-sm text-text">{notes}</p>
            </div>
          {/if}
        </Card>
      {:else}
        <Card title={t("meta.composer.channels")}>
          <ChannelChips
            channels={offered}
            bind:selected
            name="asset_ids_shown"
            disabled={!mayEdit}
          />
          {#if selected.length === 0 || refused("channels") || refused("asset_ids")}
            <p class="mt-2 text-sm text-red-600 dark:text-red-400" role="alert">
              {t(refused("asset_ids") ?? refused("channels") ?? "meta.issue.no_channel")}
            </p>
          {/if}
          {#each errors.filter((issue) => issue.field === "channels") as issue (issue.code + issue.asset_id)}
            <p class="mt-2 flex items-start gap-1.5 text-sm text-text">
              <TriangleAlert
                size={14}
                class="mt-0.5 shrink-0 text-red-600 dark:text-red-400"
                aria-hidden="true"
              />
              {issueText(issue)}
            </p>
          {/each}
        </Card>

        <Card title={t("meta.composer.text")}>
          <label for="post-body" class="sr-only">{t("meta.composer.text")}</label>
          <!-- A plain box on purpose. A post is published as the characters typed: markdown in
             here would reach a client's followers as asterisks (docs/UX.md, the one exception
             to "long-form text is the rich editor"). -->
          <textarea
            id="post-body"
            bind:value={body}
            rows="7"
            disabled={!mayEdit}
            placeholder={t("meta.composer.text_placeholder")}
            class="{inputClass} resize-y leading-relaxed"></textarea>
          <div class="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-muted">
            {#each networks as network (network)}
              {@const limit = LIMITS[network]}
              {@const text = body}
              <span
                class="inline-flex items-center gap-1 tabular-nums {text.length > limit.body
                  ? 'font-medium text-red-600 dark:text-red-400'
                  : ''}"
              >
                <ChannelMark channel={network} size={12} />
                {fmtNumber(text.length)} / {fmtNumber(limit.body)}
                {#if limit.hashtags !== null}
                  · {countHashtags(text)} / {limit.hashtags} #
                {/if}
              </span>
            {/each}
          </div>
          {#if issueFor("body") && !unwritten}
            <p class="mt-1.5 text-sm text-red-600 dark:text-red-400" role="alert">
              {issueText(issueFor("body")!)}
            </p>
          {/if}

          {#each chosen as channel (channel.id)}
            {#if chosen.length > 1}
              {@const own = overrides[channel.id] !== undefined}
              <div class="mt-4 border-t border-border pt-3">
                <label class="flex items-center gap-2 text-sm text-text">
                  <input
                    type="checkbox"
                    checked={own}
                    disabled={!mayEdit}
                    onchange={(event) => {
                      const next = { ...overrides };
                      if (event.currentTarget.checked) next[channel.id] = body;
                      else delete next[channel.id];
                      overrides = next;
                    }}
                  />
                  {t("meta.composer.own_text", { name: channelTitle(channel) })}
                </label>
                {#if own}
                  <textarea
                    rows="4"
                    disabled={!mayEdit}
                    value={overrides[channel.id]}
                    oninput={(event) =>
                      (overrides = { ...overrides, [channel.id]: event.currentTarget.value })}
                    aria-label={t("meta.composer.own_text", { name: channelTitle(channel) })}
                    class="{inputClass} mt-2 resize-y leading-relaxed"></textarea>
                  <p
                    class="mt-1 text-xs tabular-nums {(overrides[channel.id] ?? '').length >
                    LIMITS[channel.channel].body
                      ? 'font-medium text-red-600 dark:text-red-400'
                      : 'text-text-muted'}"
                  >
                    {fmtNumber((overrides[channel.id] ?? "").length)} /
                    {fmtNumber(LIMITS[channel.channel].body)}
                  </p>
                {/if}
              </div>
            {/if}
          {/each}

          <div class="mt-4 border-t border-border pt-3">
            <label for="post-link" class="mb-1 block text-sm font-medium text-text">
              {t("meta.composer.link")}
            </label>
            <input
              id="post-link"
              type="url"
              inputmode="url"
              bind:value={link}
              disabled={!mayEdit}
              placeholder="https://"
              class={inputClass}
            />
            {#if refused("link")}
              <p class="mt-1 text-sm text-red-600 dark:text-red-400" role="alert">
                {t(refused("link")!)}
              </p>
            {/if}
            {#each warnings.filter((issue) => issue.field === "link") as issue (issue.code)}
              <p class="mt-1 flex items-center gap-1.5 text-xs text-text-muted">
                {#if issue.channel}<ChannelMark channel={issue.channel} size={12} />{/if}
                {issueText(issue)}
              </p>
            {/each}
          </div>
        </Card>

        <Card title={format === "reel" ? t("meta.composer.video") : t("meta.composer.media")}>
          {#if media.length > 0}
            <ul class="grid grid-cols-2 gap-3 sm:grid-cols-3">
              {#each media as item, index (item.file_id ?? item.url)}
                <li class="min-w-0">
                  <div
                    class="group relative aspect-square overflow-hidden rounded-lg border border-border bg-surface"
                  >
                    {#if item.kind === "image" && thumb(item)}
                      <button
                        type="button"
                        class="block h-full w-full"
                        onclick={() =>
                          openLightbox(
                            media
                              .filter((m) => m.kind === "image" && m.file_id)
                              .map((m) => ({
                                src: `/api/v1/files/${m.file_id}`,
                                thumb: thumb(m) ?? "",
                                label:
                                  m.alt || t("meta.composer.image_n", { n: String(index + 1) }),
                              })),
                            media
                              .filter((m) => m.kind === "image" && m.file_id)
                              .findIndex((m) => m.file_id === item.file_id),
                          )}
                      >
                        <img
                          src={thumb(item)}
                          alt={item.alt}
                          class="h-full w-full object-cover"
                          loading="lazy"
                        />
                      </button>
                    {:else}
                      <div
                        class="flex h-full flex-col items-center justify-center gap-1 px-2 text-center"
                      >
                        <span class="text-xs font-medium text-text">{t("meta.preview.video")}</span>
                        <span class="w-full truncate text-xs text-text-muted">{item.url}</span>
                      </div>
                    {/if}
                    {#if mayEdit}
                      <div class="absolute inset-x-1 top-1 flex items-center justify-between gap-1">
                        <span class="flex gap-1">
                          <button
                            type="button"
                            class="rounded bg-black/60 px-1.5 py-0.5 text-xs text-white disabled:opacity-30"
                            disabled={index === 0}
                            aria-label={t("meta.composer.move_earlier")}
                            onclick={() => move(index, -1)}>←</button
                          >
                          <button
                            type="button"
                            class="rounded bg-black/60 px-1.5 py-0.5 text-xs text-white disabled:opacity-30"
                            disabled={index === media.length - 1}
                            aria-label={t("meta.composer.move_later")}
                            onclick={() => move(index, 1)}>→</button
                          >
                        </span>
                        <button
                          type="button"
                          class="rounded bg-black/60 p-1 text-white"
                          aria-label={t("meta.composer.remove_media")}
                          onclick={() => (media = media.filter((_, i) => i !== index))}
                        >
                          <X size={12} aria-hidden="true" />
                        </button>
                      </div>
                    {/if}
                  </div>
                  {#if item.kind === "image"}
                    <input
                      type="text"
                      value={item.alt}
                      disabled={!mayEdit}
                      maxlength="1000"
                      placeholder={t("meta.composer.alt_placeholder")}
                      aria-label={t("meta.composer.alt", { n: String(index + 1) })}
                      oninput={(event) =>
                        (media = media.map((m, i) =>
                          i === index ? { ...m, alt: event.currentTarget.value } : m,
                        ))}
                      class="mt-1.5 w-full rounded-md border border-border bg-surface-raised px-2 py-1 text-xs text-text outline-none focus:border-brand"
                    />
                    {#if item.width && item.height}
                      <p class="mt-0.5 text-[11px] tabular-nums text-text-muted">
                        {item.width} × {item.height}
                      </p>
                    {/if}
                  {/if}
                </li>
              {/each}
            </ul>
          {/if}

          {#if mayEdit}
            {#if format !== "reel"}
              <div
                class="rounded-lg border border-dashed border-border px-4 py-4 text-center {media.length >
                0
                  ? 'mt-4'
                  : ''}"
                use:filedrop={{ onerror: (key) => (localError = key) }}
              >
                <label
                  class="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-text hover:border-text-muted"
                >
                  <ImagePlus size={15} aria-hidden="true" />
                  {busy.is("upload") ? t("meta.composer.uploading") : t("meta.composer.add_image")}
                  <input
                    bind:this={fileInput}
                    type="file"
                    name="file"
                    multiple
                    accept="image/png,image/jpeg,image/webp"
                    class="sr-only"
                    disabled={busy.active}
                    onchange={upload}
                  />
                </label>
                <p class="mt-2 text-xs text-text-muted">{t("meta.composer.image_hint")}</p>
              </div>
            {/if}
            <div class="mt-4">
              <label for="post-video" class="mb-1 block text-sm font-medium text-text">
                {t("meta.composer.video_url")}
              </label>
              <div class="flex gap-2">
                <input
                  id="post-video"
                  type="url"
                  inputmode="url"
                  bind:value={videoUrl}
                  placeholder="https://"
                  class={inputClass}
                  onkeydown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      addVideo();
                    }
                  }}
                />
                <Button type="button" variant="secondary" onclick={addVideo} disabled={!videoUrl}>
                  {t("common.add")}
                </Button>
              </div>
              <p class="mt-1 text-xs text-text-muted">{t("meta.composer.video_hint")}</p>
            </div>
          {/if}

          {#if fileError}
            <p class="mt-2 text-sm text-red-600 dark:text-red-400" role="alert">{t(fileError)}</p>
          {/if}
          {#each unwritten ? [] : issues.filter((issue) => issue.field === "media") as issue (issue.code + issue.channel + JSON.stringify(issue.details))}
            <p class="mt-2 flex items-start gap-1.5 text-sm text-text">
              {#if issue.level === "error"}
                <TriangleAlert
                  size={14}
                  class="mt-0.5 shrink-0 text-red-600 dark:text-red-400"
                  aria-hidden="true"
                />
              {/if}
              {#if issue.channel}<ChannelMark
                  channel={issue.channel}
                  size={14}
                  class="mt-0.5 text-text-muted"
                />{/if}
              <span>{issueText(issue)}</span>
            </p>
          {/each}
        </Card>

        <Card title={t("meta.composer.when")}>
          {#if mayEdit}
            <div class="flex flex-wrap items-center gap-2">
              <div class="w-44">
                <DateInput name="day_shown" id="post-day" bind:value={day} />
              </div>
              <div class="w-28">
                <TimeInput name="time_shown" id="post-time" bind:value={time} />
              </div>
              {#if day}
                <button
                  type="button"
                  class="text-xs text-text-muted hover:text-text hover:underline"
                  onclick={() => (day = "")}>{t("meta.composer.clear_time")}</button
                >
              {/if}
            </div>
          {:else}
            <p class="text-sm tabular-nums text-text">
              {when ? fmtDateTime(when) : t("meta.list.no_time")}
            </p>
          {/if}
          {#if !mayEdit}
            <!-- A record: nothing to explain about a clock nobody can change. -->
          {:else if refused("scheduled_at")}
            <p class="mt-1.5 text-sm text-red-600 dark:text-red-400" role="alert">
              {t(refused("scheduled_at")!)}
            </p>
          {:else}
            <p class="mt-1.5 text-xs text-text-muted">
              {#if networks.includes("facebook") && scheduler === "meta"}
                {t("meta.composer.when_hint_meta")}
              {:else}
                {t("meta.composer.when_hint")}
              {/if}
            </p>
          {/if}

          <div class="mt-4 border-t border-border pt-3">
            <label for="post-notes" class="mb-1 block text-sm font-medium text-text">
              {t("meta.composer.notes")}
            </label>
            <textarea
              id="post-notes"
              bind:value={notes}
              rows="2"
              disabled={!mayEdit}
              placeholder={t("meta.composer.notes_placeholder")}
              class="{inputClass} resize-y"></textarea>
          </div>
        </Card>
      {/if}
    </div>

    <aside class="min-w-0 space-y-6">
      {#if open}
        <Card kind="panel" title={unwritten ? t("meta.checks.todo") : t("meta.checks.title")}>
          {#if dirty && autosaves}
            <p class="text-sm text-text-muted">{t("meta.checks.pending")}</p>
          {:else if issues.length === 0 && passed && status !== "scheduled"}
            <p class="flex items-start gap-2 text-sm text-text">
              <TriangleAlert
                size={14}
                class="mt-0.5 shrink-0 text-red-600 dark:text-red-400"
                aria-hidden="true"
              />
              {t("meta.issue.time_passed")}
            </p>
          {:else if issues.length === 0 && !day && status !== "scheduled"}
            <!-- Nothing is wrong and nothing is planned: the one step left, said where the
                 disabled button's reason would otherwise be a tooltip nobody hovers. -->
            <p class="flex items-start gap-2 text-sm text-text">
              <Circle size={14} class="mt-0.5 shrink-0 text-text-muted" aria-hidden="true" />
              {t("meta.checks.needs_time")}
            </p>
          {:else if issues.length === 0}
            <StateMark state="ok" label={t("meta.checks.ready")} />
          {:else}
            <ul class="space-y-2">
              {#each issues as issue (issue.code + issue.channel + issue.asset_id + JSON.stringify(issue.details))}
                <li class="flex items-start gap-2 text-sm text-text">
                  <!-- The glyph carries the weight and the words carry the meaning: a label
                       per line would spend half the panel saying "must be solved". -->
                  {#if unwritten}
                    <Circle size={14} class="mt-0.5 shrink-0 text-text-muted" aria-hidden="true" />
                  {:else if issue.level === "error"}
                    <TriangleAlert
                      size={14}
                      class="mt-0.5 shrink-0 text-red-600 dark:text-red-400"
                      aria-hidden="true"
                    />
                  {:else}
                    <Info size={14} class="mt-0.5 shrink-0 text-text-muted" aria-hidden="true" />
                  {/if}
                  <span class="sr-only"
                    >{unwritten
                      ? t("meta.checks.todo")
                      : issue.level === "error"
                        ? t("meta.checks.error")
                        : t("meta.checks.warning")}:</span
                  >
                  {#if issue.channel}
                    <ChannelMark channel={issue.channel} size={14} class="mt-0.5 text-text-muted" />
                  {/if}
                  <span class="min-w-0">{issueText(issue)}</span>
                </li>
              {/each}
            </ul>
          {/if}
        </Card>
      {/if}

      {#each chosen as channel (channel.id)}
        <div>
          <p class="mb-1.5 text-xs font-medium text-text-muted">
            {channelTitle(channel)}
          </p>
          <PostPreview
            channel={channel.channel}
            name={channel.name}
            username={channel.username}
            pictureUrl={channel.pictureUrl}
            body={overrides[channel.id] ?? body}
            link={link || null}
            media={previewMedia}
            {format}
            when={post.published_at
              ? fmtDateTime(post.published_at)
              : post.scheduled_at && status === "scheduled"
                ? fmtDateTime(post.scheduled_at)
                : null}
          />
        </div>
      {/each}

      <Card kind="register" title={t("meta.composer.record")}>
        <dl class="space-y-1.5 text-sm">
          <div class="flex justify-between gap-3">
            <dt class="text-text-muted">{t("meta.list.author")}</dt>
            <dd class="truncate text-text">{post.created_by_name || t("activity.system")}</dd>
          </div>
          {#if post.approved_by_name}
            <div class="flex justify-between gap-3">
              <dt class="text-text-muted">{t("meta.list.approver")}</dt>
              <dd class="truncate text-text" title={post.approved_at ?? undefined}>
                {post.approved_by_name}
                {#if post.approved_at}
                  <span class="text-text-muted">· {fmtRelativeTime(post.approved_at)}</span>
                {/if}
              </dd>
            </div>
          {/if}
          <div class="flex justify-between gap-3">
            <dt class="text-text-muted">{t("meta.list.created")}</dt>
            <dd class="tabular-nums text-text">{fmtDateTime(post.created_at)}</dd>
          </div>
        </dl>
        {#if trail.length > 0}
          <div class="mt-4 border-t border-border pt-3">
            <ActivityFeed items={trail} />
          </div>
        {/if}
      </Card>
    </aside>
  </div>
</form>

<form
  bind:this={duplicateForm}
  method="POST"
  action="?/duplicate"
  use:enhance={busy.clear("duplicate")}
  class="hidden"
></form>

<ConfirmDialog
  bind:open={confirmingPublish}
  title={t("meta.confirm.publish_title")}
  message={t("meta.confirm.publish_message")}
  consequences={chosen.map((channel) =>
    t("meta.confirm.publish_channel", {
      name: channel.name,
      channel: channelLabel(channel.channel),
    }),
  )}
  action="?/publish"
  {fields}
  confirmLabel={t("meta.action.publish_now")}
  variant="primary"
  confirmDisabled={selected.length === 0}
  onfailure={(key) => (dialogError = key)}
  onsuccess={() => {
    dialogError = null;
    saved = serialised;
  }}
/>

<ConfirmDialog
  bind:open={confirmingCancel}
  title={t("meta.confirm.cancel_title")}
  message={t("meta.confirm.cancel_message")}
  action="?/cancel"
  confirmLabel={t("meta.action.cancel")}
  onfailure={(key) => (dialogError = key)}
  onsuccess={() => (dialogError = null)}
/>

<ConfirmDialog
  bind:open={confirmingDelete}
  title={t("meta.confirm.delete_title")}
  message={t("meta.confirm.delete_message")}
  action="?/delete"
  onfailure={(key) => (dialogError = key)}
/>
