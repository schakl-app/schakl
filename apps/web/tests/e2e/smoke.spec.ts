import { expect, test } from "@playwright/test";

/**
 * The first smoke tests. They assert the shell stands up — SSR renders, the tenant resolves
 * from the hostname, and the auth guard fires — without assuming the database has been seeded
 * with any particular org, user or company.
 *
 * Selectors are `name=` attributes, never visible text: the UI ships in Dutch by default and
 * every string goes through Paraglide (CLAUDE.md §2), so matching on text would be asserting
 * the translation, not the behaviour.
 */

test("an anonymous visitor is sent somewhere sensible, not to a 500", async ({ page }) => {
  const response = await page.goto("/");
  expect(response?.status()).toBeLessThan(400);

  // `(app)/+layout.server.ts` redirects anonymous users to /login; an instance with no org at
  // all goes to the first-run wizard instead (issue #26). Both are correct; a 500 is not.
  await expect(page).toHaveURL(/\/(login|setup)(\?.*)?$/);
});

test("a deep link is remembered across the guard, not thrown away", async ({ page }) => {
  // The bug this pins: `(app)/+layout.server.ts` used to redirect to a bare `/login`, so every
  // guarded link anyone pasted into a chat or bookmarked landed the recipient on the dashboard
  // with no way back but navigating there by hand. The reading side always worked — only the
  // producer was missing, which is exactly the kind of half-feature no functional test notices.
  const target = "/companies?status=active";
  const response = await page.goto(target);
  expect(response?.status()).toBeLessThan(400);

  const url = new URL(page.url());
  // An instance with no org at all goes to the first-run wizard instead (issue #26); there is no
  // session to come back to, so it carries nothing and that is correct.
  if (url.pathname === "/setup") return;

  expect(url.pathname).toBe("/login");
  expect(url.searchParams.get("next")).toBe(target);
  // And the form carries it onward: an action posts to `?/login`, so the page's own query string
  // is not on the request that redeems the credentials.
  await expect(page.locator("input[name=next]")).toHaveValue(target);
});

test("the login screen renders its form", async ({ page }) => {
  await page.goto("/login");

  await expect(page.locator("input[name=email]")).toBeVisible();
  await expect(page.locator("input[name=password]")).toBeVisible();
  await expect(page.locator("button[type=submit]")).toBeEnabled();
});

test("the tenant brand colour is stamped on the document", async ({ page }) => {
  await page.goto("/login");

  // `hooks.server.ts` inlines the theme into <html> so there is no flash of the wrong brand.
  // If this is empty, tenant resolution silently fell back and white-labelling is broken.
  const brand = await page.evaluate(() =>
    getComputedStyle(document.documentElement).getPropertyValue("--brand-primary").trim(),
  );
  expect(brand).not.toBe("");
});
