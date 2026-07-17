/**
 * Cloud posture e2e (epic #199) — drives a FRESH stack running the cloud-dev overlay:
 *
 *   docker compose -f infra/compose.yaml -f infra/compose.cloud-dev.yaml up -d   # empty DB!
 *   PLAYWRIGHT_CLOUD=1 pnpm web test:e2e tests/e2e/cloud.spec.ts
 *
 * Run this file alone: the smoke suite expects a stack whose first-run already happened,
 * while this one drives that first run itself. Skipped without PLAYWRIGHT_CLOUD=1 so the
 * committed smoke suite keeps passing against a self-host stack. The flow covers the whole operator story end-to-end: cloud first-run
 * (instance owner only) → console → instance API key → provisioning API → tenant login on
 * the org's own subdomain → org-issued service PIN → PIN unlock in the console.
 *
 * Selectors are name=/role-based, never visible text (the UI ships in Dutch — docs/WORKFLOW.md).
 */
import { expect, test } from "@playwright/test";

const CLOUD = process.env.PLAYWRIGHT_CLOUD === "1";
// The instance console lives on the apex (base domain); orgs live on subdomains of it.
const APEX = process.env.PLAYWRIGHT_CLOUD_APEX ?? "http://localhost";
const orgUrl = (slug: string) => APEX.replace("://", `://${slug}.`);

const RUN = `${Date.now()}`.slice(-6);
const OWNER = { email: `operator-${RUN}@example.com`, password: "supersecret-1" };
const ORG = {
  slug: `agency-${RUN}`,
  name: `Agency ${RUN}`,
  owner: `boss-${RUN}@example.com`,
  password: "orgsecret-12",
};

test.describe.configure({ mode: "serial" });

/** Fill after hydration: SvelteKit re-renders the form when hydration lands, which can wipe
 * a value typed into the SSR DOM (the flake this replaces). */
async function fillSettled(page: import("@playwright/test").Page, selector: string, value: string) {
  await page.waitForLoadState("networkidle");
  await page.locator(selector).fill(value);
  await expect(page.locator(selector)).toHaveValue(value);
}

async function consoleSignIn(page: import("@playwright/test").Page) {
  await page.goto(APEX + "/console");
  await page.waitForLoadState("networkidle");
  if (/\/console\/login/.test(page.url())) {
    await fillSettled(page, "input[name=email]", OWNER.email);
    await fillSettled(page, "input[name=password]", OWNER.password);
    await page.locator("button[type=submit]").click();
    await page.waitForURL(/\/console(\?.*)?$/);
  }
}

test.describe("cloud console", () => {
  test.skip(!CLOUD, "needs the cloud-dev stack and PLAYWRIGHT_CLOUD=1");

  test("a fresh cloud install runs owner-only setup and lands in the console", async ({ page }) => {
    await page.goto(APEX + "/");
    // Fresh install → the wizard; already set up (a re-run) → the console login.
    await page.waitForURL(/\/(setup|console(\/login)?)(\?.*)?$/);
    if (page.url().includes("/setup")) {
      // Cloud setup shows no org fields — only the instance-owner account.
      await expect(page.locator("input[name=org_name]")).toHaveCount(0);
      await fillSettled(page, "input[name=owner_email]", OWNER.email);
      await fillSettled(page, "input[name=owner_password]", OWNER.password);
      await page.locator("form button[type=submit]").last().click();
      await page.waitForURL(/\/console(\?.*)?$/);
    }
  });

  test("the console signs the instance owner in on the apex host", async ({ page }) => {
    await consoleSignIn(page);
    await expect(page.locator("button[name=new_org]")).toBeVisible();
  });

  test("an instance API key provisions an org over the API", async ({ page, request }) => {
    await consoleSignIn(page);

    // Mint a provisioning credential in the console UI; the secret is shown exactly once.
    await page.goto(APEX + "/console/keys");
    await page.locator("input[name=name]").fill(`e2e-${RUN}`);
    await page.locator("form[action*=create] button").click();
    const secret = (await page.locator("code").first().textContent())?.trim() ?? "";
    expect(secret).toMatch(/^schakl_/);

    // Auto-configure a new org (with a working owner password) through the API.
    const created = await request.post(`${APEX}/api/v1/instance/provisioning/orgs`, {
      headers: { "X-API-Key": secret },
      data: {
        name: ORG.name,
        slug: ORG.slug,
        owner_email: ORG.owner,
        owner_password: ORG.password,
        plan: "trial",
      },
    });
    expect(created.status()).toBe(201);
    const body = await created.json();
    expect(body.plan).toBe("trial");
    expect(body.trial_ends_at).toBeTruthy();

    // The org now shows on the console list with a link to its detail page.
    await page.goto(APEX + "/console");
    await expect(page.locator(`a[href="/console/orgs/${body.id}"]`)).toBeVisible();
  });

  test("the org resolves on its own subdomain and issues a service PIN", async ({ page }) => {
    await page.goto(orgUrl(ORG.slug) + "/login");
    await expect(page.locator("input[name=email]")).toBeVisible();
    await fillSettled(page, "input[name=email]", ORG.owner);
    await fillSettled(page, "input[name=password]", ORG.password);
    await page.locator("button[type=submit]").click();
    await page.waitForURL((url) => !url.pathname.startsWith("/login"));

    // Instellingen → Service-toegang: generate the PIN (shown once) and stash it.
    await page.goto(orgUrl(ORG.slug) + "/settings/service-access");
    await page.locator("form[action*=generate] button").click();
    const pin = (await page.locator("code").first().textContent())?.trim() ?? "";
    expect(pin).toMatch(/^\d{4}-\d{4}-\d{4}$/);
    process.env.E2E_SERVICE_PIN = pin;
  });

  test("the console unlocks the org's data only with the PIN", async ({ page }) => {
    const pin = process.env.E2E_SERVICE_PIN ?? "";
    expect(pin).not.toBe("");

    await consoleSignIn(page);
    const row = page.locator("tr", { hasText: ORG.name });
    await row.locator(`a[href^="/console/orgs/"]`).click();

    // Locked: the PIN form is the only way in; the member list is absent.
    const pinInput = page.locator("input[name=pin]");
    await expect(pinInput).toBeVisible();

    // A wrong PIN is refused…
    await pinInput.fill("0000-0000-0000");
    await page.locator("form[action*=unlock] button").click();
    await expect(page.locator("input[name=pin]")).toBeVisible();

    // …the real one unlocks the tenant data (member rows with an impersonate action).
    await page.locator("input[name=pin]").fill(pin);
    await page.locator("form[action*=unlock] button").click();
    await expect(page.locator("form[action*=impersonate]").first()).toBeVisible();
  });
});
