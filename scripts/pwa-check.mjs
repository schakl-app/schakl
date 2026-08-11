#!/usr/bin/env node
// pwa:check — the service worker is generated, installed, and able to finish evaluating.
//
// Run against the *build output*, after `vite build`, because every one of these faults is
// invisible in the source and invisible at runtime:
//
//   1. The app shipped a generated `sw.js` that nothing ever registered. `injectRegister:
//      "auto"` injects its <script> through Vite's `transformIndexHtml`, and SvelteKit bakes
//      `app.html` without ever calling that hook — so `/sw.js` answered 200 for months and no
//      browser had ever been asked for it. The only visible symptom was one settings screen
//      saying browser notifications were unavailable.
//   2. The path it would have used was relative (`./sw.js`, `scope: "./"`), which resolves
//      against the *page*: `/instellingen/sw.js` on the settings screen.
//   3. `createHandlerBoundToURL("/")` — the plugin's default navigation fallback — throws
//      `non-precached-url` while the worker evaluates, because nothing prerenders "/" into the
//      precache. The throw lands inside a promise chain, so it is neither a failed install nor
//      a console error: the worker's last line simply never runs.
//   4. `registerType: "autoUpdate"` is delivered as `skipWaiting` + `clientsClaim`, and the
//      plugin only sets them while `injectRegister` is left at its default. Turn that off and
//      "autoUpdate" becomes a word in a config file: a new deploy's worker queues behind the
//      old one forever.
//
// Unit tests cannot see any of this — they stub `navigator.serviceWorker`, which is exactly the
// thing that was missing. Assert the wiring, not the artefact (`docs/MCP.md` has the same rule
// for a route only reachable through a proxy).

import { readdirSync, readFileSync, existsSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const CLIENT = join(ROOT, "apps/web/.svelte-kit/output/client");

const problems = [];
const fail = (message) => problems.push(message);

if (!existsSync(CLIENT)) {
  console.error(`pwa:check — no build output at ${CLIENT}. Run \`pnpm web build\` first.`);
  process.exit(1);
}

// --------------------------------------------------------------------------- //
// The worker itself
// --------------------------------------------------------------------------- //
const swPath = join(CLIENT, "sw.js");
if (!existsSync(swPath)) {
  fail("no `sw.js` in the client output — the PWA plugin generated no service worker.");
}
const sw = existsSync(swPath) ? readFileSync(swPath, "utf8") : "";

// Everything the worker pulls in at install has to be there, or the whole worker fails to
// evaluate. `static/push-sw.js` carries the `push` + `notificationclick` listeners (#309).
for (const [, imported] of sw.matchAll(/importScripts\("(\/[^"]+)"\)/g)) {
  if (!existsSync(join(CLIENT, imported.slice(1)))) {
    fail(`\`sw.js\` importScripts ${imported}, which is not in the client output.`);
  }
}
if (sw && !sw.includes('importScripts("/push-sw.js")')) {
  fail("`sw.js` no longer imports `/push-sw.js`: browser push has no handlers (docs/WEBPUSH.md).");
}

// A URL bound to a precache handler must be *in* the precache, or workbox throws while the
// worker evaluates and every line after it is silently skipped.
const precached = new Set([...sw.matchAll(/url:"([^"]*)"/g)].map(([, url]) => url));
for (const [, bound] of sw.matchAll(/createHandlerBoundToURL\("([^"]*)"\)/g)) {
  const key = bound.replace(/^\//, "");
  if (!precached.has(key) && !precached.has(bound)) {
    fail(
      `\`sw.js\` binds a handler to ${bound}, which nothing precaches — workbox throws ` +
        "`non-precached-url` there and the rest of the worker never runs.",
    );
  }
}

// `registerType: "autoUpdate"`, stated where it takes effect.
for (const call of ["skipWaiting()", "clientsClaim()"]) {
  if (sw && !sw.includes(call)) {
    fail(`\`sw.js\` never calls ${call}: a new deploy's worker would queue behind the old one.`);
  }
}

// --------------------------------------------------------------------------- //
// Somebody installs it
// --------------------------------------------------------------------------- //
function walk(dir) {
  let out = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) out = out.concat(walk(path));
    else if (name.endsWith(".js")) out.push(path);
  }
  return out;
}

const bundles = existsSync(join(CLIENT, "_app")) ? walk(join(CLIENT, "_app")) : [];
const registrars = bundles.filter((path) =>
  readFileSync(path, "utf8").includes("serviceWorker.register"),
);
if (registrars.length === 0) {
  fail(
    "no client bundle calls `serviceWorker.register`: the worker is built and served, and no " +
      "browser is ever asked to install it (src/lib/core/pwa.ts).",
  );
}
for (const path of registrars) {
  const source = readFileSync(path, "utf8");
  if (!source.includes('"/sw.js"') && !source.includes("'/sw.js'")) {
    fail(
      `${path.slice(ROOT.length + 1)} registers a worker by some path other than "/sw.js" — a ` +
        "relative one resolves against whichever page is open.",
    );
  }
}

if (problems.length) {
  console.error("pwa:check failed:\n");
  for (const problem of problems) console.error(`  • ${problem}`);
  console.error("");
  process.exit(1);
}

console.log(`pwa:check — worker installs itself, imports its listeners, evaluates to the end.`);
