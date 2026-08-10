/**
 * Browser-side Web Push enrolment (#309).
 *
 * The service worker's other half lives in `static/push-sw.js`; this is what talks to it and to
 * the API. Everything here is browser-only — it touches `Notification`, `navigator` and
 * `PushManager` — so it must never be imported from a `load` that also runs on the server.
 *
 * Two rules shape the whole file:
 *
 *  - **Never prompt on load.** `Notification.requestPermission()` fired by a page render is the
 *    dark pattern browsers penalise, and it burns the one chance the user has to say yes at a
 *    moment they were not asking a question. Every path to a prompt starts at a click.
 *  - **Re-present an existing subscription on every session.** A push endpoint rotates without
 *    telling anyone, and a rotated endpoint that nobody re-registered is a device that has
 *    quietly stopped receiving. `refresh()` is what stops "it worked last week" from being a
 *    silent lie; it costs one call, and only for people who already granted permission.
 */

/** What the settings screen renders. Each state is a different sentence, not a disabled button. */
export type PushState =
  | "unsupported" // no service worker or no PushManager: nothing to offer
  | "needs-install" // iOS: Web Push exists only inside a home-screen PWA
  | "denied" // the browser's own setting, which we cannot reopen
  | "off" // supported, permitted or not asked, not subscribed here
  | "on"; // this browser is registered

export interface PushStatus {
  state: PushState;
  /** The endpoint of this browser's subscription, so the device list can mark its own row. */
  endpoint: string | null;
}

/**
 * iOS supports Web Push from 16.4, but **only** inside an installed PWA. Offering a button that
 * silently fails is worse than saying so, so Safari-on-iOS outside standalone display mode gets
 * its own state and its own sentence.
 */
function needsInstall(): boolean {
  if (typeof window === "undefined") return false;
  const standalone =
    window.matchMedia?.("(display-mode: standalone)").matches ||
    // Safari's own, non-standard flag; it predates and still outlives the media query on iOS.
    (window.navigator as { standalone?: boolean }).standalone === true;
  if (standalone) return false;
  const ua = window.navigator.userAgent;
  return /iPad|iPhone|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
}

function supported(): boolean {
  return (
    typeof window !== "undefined" &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

/** The VAPID key travels as base64url; `pushManager.subscribe` insists on raw bytes. */
function decodeKey(base64url: string): Uint8Array {
  const padded = base64url.replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(padded + "=".repeat((4 - (padded.length % 4)) % 4));
  return Uint8Array.from(raw, (char) => char.charCodeAt(0));
}

/**
 * A short, honest device label. Deliberately crude: the API never parses a user agent, because
 * that is a losing arms race, and this only has to answer "which of my four browsers is this?".
 */
function deviceLabel(): string {
  const ua = navigator.userAgent;
  const browser = /Edg\//.test(ua)
    ? "Edge"
    : /OPR\//.test(ua)
      ? "Opera"
      : /Firefox\//.test(ua)
        ? "Firefox"
        : /Chrome\//.test(ua)
          ? "Chrome"
          : /Safari\//.test(ua)
            ? "Safari"
            : "Browser";
  const platform = /Android/.test(ua)
    ? "Android"
    : /iPhone|iPad|iPod/.test(ua)
      ? "iOS"
      : /Windows/.test(ua)
        ? "Windows"
        : /Mac OS X/.test(ua)
          ? "macOS"
          : /Linux/.test(ua)
            ? "Linux"
            : "";
  return platform ? `${browser} — ${platform}` : browser;
}

async function registration(): Promise<ServiceWorkerRegistration | null> {
  if (!supported()) return null;
  try {
    return await navigator.serviceWorker.ready;
  } catch {
    return null;
  }
}

/** What this browser's situation currently is. Reads only — never prompts, never subscribes. */
export async function status(): Promise<PushStatus> {
  if (!supported()) {
    return { state: needsInstall() ? "needs-install" : "unsupported", endpoint: null };
  }
  if (Notification.permission === "denied") return { state: "denied", endpoint: null };
  const reg = await registration();
  const subscription = (await reg?.pushManager.getSubscription()) ?? null;
  return {
    state: subscription ? "on" : "off",
    endpoint: subscription?.endpoint ?? null,
  };
}

/**
 * Ask for permission and register this browser. **Must be called from a user gesture** — the
 * permission prompt is only offered to a click, and this is the only function that prompts.
 */
export async function subscribe(): Promise<PushStatus> {
  if (!supported()) {
    return { state: needsInstall() ? "needs-install" : "unsupported", endpoint: null };
  }
  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    return { state: permission === "denied" ? "denied" : "off", endpoint: null };
  }

  const reg = await registration();
  if (!reg) return { state: "unsupported", endpoint: null };

  const config = await fetch("/api/v1/notifications/push/config");
  if (!config.ok) return { state: "off", endpoint: null };
  const { vapid_public_key } = (await config.json()) as { vapid_public_key: string };

  // An existing subscription is reused rather than re-minted: re-subscribing with a different
  // key would orphan whatever the server still has, and the key never changes anyway.
  const subscription =
    (await reg.pushManager.getSubscription()) ??
    (await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: decodeKey(vapid_public_key) as BufferSource,
    }));

  await register(subscription);
  return { state: "on", endpoint: subscription.endpoint };
}

/** POST a subscription to the API. Idempotent server-side: the endpoint identifies the device. */
async function register(subscription: PushSubscription): Promise<void> {
  const json = subscription.toJSON();
  await fetch("/api/v1/notifications/push/subscriptions", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      endpoint: subscription.endpoint,
      p256dh: json.keys?.p256dh,
      auth: json.keys?.auth,
      user_agent: deviceLabel(),
    }),
  });
}

/**
 * Re-present an already-granted subscription, so a rotated endpoint does not become a device
 * that silently stopped receiving. Called once per session from the app layout; a no-op — and
 * **no network call at all** — for anyone who has not granted permission.
 */
export async function refresh(): Promise<void> {
  if (!supported() || Notification.permission !== "granted") return;
  const reg = await registration();
  const subscription = await reg?.pushManager.getSubscription();
  if (!subscription) return;
  await register(subscription);
}

/** Unregister this browser, both sides. The API is told by endpoint — all the browser still knows. */
export async function unsubscribe(): Promise<PushStatus> {
  const reg = await registration();
  const subscription = await reg?.pushManager.getSubscription();
  if (subscription) {
    const endpoint = subscription.endpoint;
    await subscription.unsubscribe();
    await fetch("/api/v1/notifications/push/unsubscribe", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ endpoint }),
    });
  }
  return { state: "off", endpoint: null };
}
