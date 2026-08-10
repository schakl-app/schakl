/**
 * Service-worker half of browser push notifications (#309).
 *
 * Imported into the workbox-generated service worker by `workbox.importScripts` in
 * `vite.config.ts` rather than replacing it. The alternative — switching the PWA plugin to
 * `injectManifest` and owning `src/sw.ts` — would hand us the precache manifest and every
 * runtime caching strategy workbox currently writes for us, in an app that is already installed
 * on real devices. That is a disproportionate amount of regression surface for two event
 * listeners, so this file stays plain JS in `static/` and the generated worker keeps its job.
 *
 * Two rules the browser enforces and this file must not break:
 *
 *  - **A push always shows a notification.** Receiving one and showing nothing is a spec
 *    violation, and browsers respond by revoking the permission. So every failure path here
 *    still ends in `showNotification`, including a payload that will not parse.
 *  - **The work must be inside `waitUntil`.** A promise the worker is not told to wait for is a
 *    worker that may be killed mid-notification.
 *
 * Nothing here is branded at build time (Golden Rule 4): the title, body, deep link and icon all
 * arrive in the encrypted payload, resolved per tenant at send time.
 */

/* global self, clients */

// Only ever a last resort: the bundled mark, for a payload that carried no tenant icon.
const FALLBACK_ICON = "/icons/icon-192.png";

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    // Unparseable payload: still show something rather than nothing, or the browser will
    // eventually take the permission away over it.
    data = {};
  }

  const title = data.title || "schakl";
  const options = {
    body: data.body || "",
    icon: data.icon || FALLBACK_ICON,
    badge: FALLBACK_ICON,
    // A stable tag makes a second push *replace* the first instead of stacking three lock-screen
    // entries that each say a version of the same thing.
    tag: data.tag || "schakl-notifications",
    renotify: true,
    timestamp: Date.now(),
    data: { url: data.url || "/" },
  };

  event.waitUntil(
    self.registration.showNotification(title, options).then(() => {
      // The unread badge on the installed app icon, where the platform has one. Unsupported
      // everywhere else, so it must never be the thing that rejects the waitUntil chain.
      if (typeof data.count === "number" && self.navigator && self.navigator.setAppBadge) {
        return self.navigator.setAppBadge(data.count).catch(() => {});
      }
      return undefined;
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || "/";

  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
      // Focusing a tab the person already has open beats opening a second copy of the app they
      // are looking at. Only same-origin windows are candidates, which is all `matchAll` returns.
      for (const client of windows) {
        if ("focus" in client) {
          if ("navigate" in client) {
            return client.focus().then(() => client.navigate(target));
          }
          return client.focus();
        }
      }
      return clients.openWindow(target);
    }),
  );
});
