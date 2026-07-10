import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright drives the **running dev stack**, not a server it starts itself.
 *
 * There is deliberately no `webServer` here. SSR resolves the tenant from the *hostname*
 * (CLAUDE.md §5, and `hooks.server.ts`): a verified custom domain or `<slug>.<base_domain>`.
 * A `vite preview` on `localhost:4173` resolves to no org, so every request would be bounced
 * to `/setup` and the suite would test the first-run wizard forever. The app also needs the
 * API, Postgres and Redis to answer anything at all.
 *
 * So: bring the stack up (`docker compose -f infra/compose.yaml up -d`), then run the tests.
 * The default target matches the README's `http://app.localhost`. If you set
 * `TRAEFIK_HTTP_PORT` (rootless podman can't bind :80), pass the port through:
 *
 *   PLAYWRIGHT_BASE_URL=http://app.localhost:8080 pnpm --filter @schakl/web test:e2e
 */
const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? "http://app.localhost";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,

  // A stray `test.only` must never silently green a CI run.
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : [["list"]],

  use: {
    baseURL,

    // The default UI language is Dutch (CLAUDE.md §8) and the app formats against
    // Europe/Amsterdam. Pin both, or a test that passes on a Dutch laptop fails in CI.
    locale: "nl-NL",
    timezoneId: "Europe/Amsterdam",

    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },

  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
