/**
 * Telling the *other* tabs (#, `SessionGuard`).
 *
 * Signing out is a one-line change to a cookie every tab shares, and the only tab that finds
 * out is the one that clicked. The rest go on drawing a working CRM — the sidebar, the client
 * list, the ⋯ menus — over a session that no longer exists, until something happens to make
 * them ask. Every control on them refuses, and none of them can say why. That is the bug: not
 * that the data is stale, but that the screen keeps *claiming* to be signed in.
 *
 * Two signals answer it, and they are deliberately different in kind:
 *
 * * **A broadcast**, for the case in the report — you signed out in a tab of this browser. It
 *   is instant, it is free, and it costs no request at all. `BroadcastChannel` is same-origin
 *   by definition, so a message here can only have come from another tab of this app.
 * * **A probe**, for every other way a session ends — an expired token, a sign-out on another
 *   device, an admin revoking access, a cleared cookie jar. There is no message to receive for
 *   those, so a tab asks: on returning to the foreground, and no more often than
 *   {@link PROBE_INTERVAL_MS}. Not a poll. A tab left open overnight makes no requests, and
 *   the moment worth asking about is the moment somebody looks at it again.
 *
 * The messages are advisory in one direction only. `signed-out` raises the prompt without
 * confirming anything, because the tab that sent it had just deleted the cookie and a wrongly
 * raised prompt costs one dismissal; `signed-in` clears it and re-reads the page, because the
 * data behind it may now belong to a *different* person.
 *
 * The third signal is not ours and costs nothing: any same-origin proxy route that already
 * answers `401 errors.unauthorized` when `locals.user` is missing knows the session is gone the
 * moment it asks. {@link reportUnauthorized} lets such a caller say so — which is what covers
 * the one gap the two above leave, a tab you are *looking at* and not switching away from.
 */
const CHANNEL = "schakl:session";

/** In-tab delivery, for {@link reportUnauthorized}: a BroadcastChannel does not talk to itself. */
const LOCAL_EVENT = "schakl:session-ended";

/** How stale a "yes, still signed in" may be before a returning tab asks again. */
export const PROBE_INTERVAL_MS = 20_000;

export type SessionMessage = { kind: "signed-out" } | { kind: "signed-in" };

export interface SessionState {
  signedIn: boolean;
  /** Only when asked for with `options` — what the re-login dialog may draw. */
  localLogin?: boolean;
  oidcEnabled?: boolean;
  oidcName?: string | null;
}

let channel: BroadcastChannel | null = null;

function bus(): BroadcastChannel | null {
  if (typeof BroadcastChannel === "undefined") return null;
  channel ??= new BroadcastChannel(CHANNEL);
  return channel;
}

/**
 * Say that this browser's session just ended.
 *
 * Called from the sign-out control *as it submits*, not after: the tab that sends this is on
 * its way to `/login` and will not be around for a callback. A `postMessage` is queued to the
 * other tabs' event loops before the navigation starts, so it survives the unload.
 */
export function announceSignedOut(): void {
  bus()?.postMessage({ kind: "signed-out" } satisfies SessionMessage);
}

/** Say that somebody is signed in here again — a tab showing the prompt can stand down. */
export function announceSignedIn(): void {
  bus()?.postMessage({ kind: "signed-in" } satisfies SessionMessage);
}

/**
 * A same-origin request just came back `401`, so this tab's session is over.
 *
 * Free liveness: the notification bell already polls every minute, and its proxy route already
 * answers 401 on a missing `locals.user`. A caller that has that answer in hand should not throw
 * it away — this is the case neither other signal catches, because a tab somebody is *reading*
 * fires no `focus` and receives no broadcast.
 *
 * Not broadcast: what one tab observed is the shared cookie's state, so the other tabs will
 * reach the same answer on their own terms, and a wrongly-broadcast verdict would raise a wall
 * everywhere at once. The guard re-probes before it settles, so a stray 401 self-corrects.
 */
export function reportUnauthorized(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(LOCAL_EVENT));
}

/** Listen for all three signals. Returns the unsubscribe, for an `$effect` teardown. */
export function onSessionMessage(handler: (message: SessionMessage) => void): () => void {
  const local = () => handler({ kind: "signed-out" });
  window.addEventListener(LOCAL_EVENT, local);

  const target = bus();
  const listener = (event: MessageEvent) => {
    const message = event.data as SessionMessage | null;
    if (message?.kind === "signed-out" || message?.kind === "signed-in") handler(message);
  };
  target?.addEventListener("message", listener);

  return () => {
    window.removeEventListener(LOCAL_EVENT, local);
    target?.removeEventListener("message", listener);
  };
}

/**
 * Ask the server whether this browser is still signed in.
 *
 * **A failed probe is not a verdict.** A dropped connection, a restarting API, a proxy blip —
 * none of those mean the session ended, and answering them with a sign-in wall over a page
 * that was working a second ago is a far worse failure than answering late. So anything short
 * of a clear "no" reads as "yes, keep going", and the next probe asks again.
 */
export async function probeSession(withOptions = false): Promise<SessionState> {
  try {
    const response = await fetch(`/session${withOptions ? "?options=1" : ""}`, {
      headers: { accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) return { signedIn: true };
    const state = (await response.json()) as SessionState;
    return typeof state?.signedIn === "boolean" ? state : { signedIn: true };
  } catch {
    return { signedIn: true };
  }
}
