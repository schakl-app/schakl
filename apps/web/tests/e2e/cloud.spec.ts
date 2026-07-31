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
 * the org's own subdomain → org-issued service PIN → PIN unlock in the console → impersonating
 * a member across the host boundary (#288), which is the one step that cannot work by sharing
 * cookies and is therefore asserted against the browser's real cookie jar.
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

/** Fill a whole form and make the values *stick* before the caller submits.
 *
 * Hydration re-applies each input's server-rendered `value={…}`, which blanks anything typed
 * before it lands — and `networkidle` is not that moment. Filling field by field and asserting
 * once was the flake: the first field was empty again by the time the second was typed, the
 * browser's own `required` check then silently swallowed the submit, and the test waited for a
 * navigation that could never happen. So fill everything, wait past a late re-render, and
 * converge until every value survives. */
async function fillSettled(page: import("@playwright/test").Page, values: Record<string, string>) {
  await page.waitForLoadState("networkidle");
  const entries = Object.entries(values);
  const fill = async () => {
    for (const [selector, value] of entries) {
      const field = page.locator(selector);
      if ((await field.inputValue()) !== value) await field.fill(value);
    }
  };
  await expect(async () => {
    await fill();
    // Long enough for a hydration re-render to have happened, so what we assert next is the
    // form as it will be at submit time rather than a millisecond of optimism.
    await page.waitForTimeout(400);
    for (const [selector, value] of entries) {
      expect(await page.locator(selector).inputValue()).toBe(value);
    }
  }).toPass({ timeout: 20_000 });
}

async function consoleSignIn(page: import("@playwright/test").Page) {
  await page.goto(APEX + "/console");
  await page.waitForLoadState("networkidle");
  if (/\/console\/login/.test(page.url())) {
    await fillSettled(page, {
      "input[name=email]": OWNER.email,
      "input[name=password]": OWNER.password,
    });
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
      await fillSettled(page, {
        "input[name=owner_email]": OWNER.email,
        "input[name=owner_password]": OWNER.password,
      });
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
    await fillSettled(page, {
      "input[name=email]": ORG.owner,
      "input[name=password]": ORG.password,
    });
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

  test("impersonation crosses to the org's host, once, and returns", async ({ page, context }) => {
    // The bug in #288, as a browser sees it. Two *different* hosts is the whole point: the
    // console's session cookie is host-only, so nothing the apex holds authenticates anything on
    // the org's hostname. Start from an empty jar and sign in to the console only — an operator
    // has never signed in on a customer's host, while an earlier test in this file did (as the
    // org's own owner), and that leftover session would authenticate the landing for the wrong
    // reason.
    await context.clearCookies();
    await consoleSignIn(page);

    // Read the jar by exact host: `context.cookies(url)` matches by domain *suffix*, so on
    // `<slug>.localhost` it happily reports the apex's own cookies and would assert nothing.
    const apexHost = new URL(APEX).hostname;
    const orgHost = new URL(orgUrl(ORG.slug)).hostname;
    const jar = async (host: string) =>
      (await context.cookies()).filter((cookie) => cookie.domain === host);
    const apexAuth = (await jar(apexHost)).find((c) => c.name === "schakl_auth");
    expect(apexAuth, "the console session must exist on the apex").toBeTruthy();
    expect((await jar(orgHost)).map((c) => c.name)).not.toContain("schakl_auth");

    // And the boundary as the *server* sees it, which is the only account that matters: the org
    // host does not know this browser at all yet.
    await page.goto(orgUrl(ORG.slug) + "/");
    await page.waitForURL(/\/login/);

    // Capture the crossing URL as the browser follows it, so the replay below uses the real
    // ticket rather than a fabricated one.
    let handoffUrl = "";
    page.on("request", (request) => {
      if (request.url().includes("/impersonate?ticket=")) handoffUrl = request.url();
    });

    await page.goto(APEX + "/console");
    const row = page.locator("tr", { hasText: ORG.name });
    await row.locator(`a[href^="/console/orgs/"]`).click();
    await page.waitForLoadState("networkidle");
    await page.locator("form[action*=impersonate] button").first().click();

    // An enhanced submit navigates on its own; a click that beat hydration lands back on the
    // console with the address as a link (the fallback the CSP forces — a form cannot redirect
    // off-origin). Either way the operator gets across, so accept both.
    const continueLink = page.locator('[data-testid="handoff-continue"]');
    if (await continueLink.isVisible().catch(() => false)) await continueLink.click();

    // Landed on the org's own host as the member — not on its login screen (the symptom).
    await page.waitForURL((url) => url.host.startsWith(`${ORG.slug}.`));
    expect(new URL(page.url()).pathname).not.toMatch(/^\/(login|impersonate)/);
    await expect(page.locator('form[action="/impersonation/stop"]')).toBeVisible();

    // Both cookies are now on the org host, and the session there is its *own*: the console's
    // cookie did not travel, one was minted for this host and this window.
    const orgCookies = await jar(orgHost);
    expect(orgCookies.map((c) => c.name)).toEqual(
      expect.arrayContaining(["schakl_auth", "schakl_impersonate"]),
    );
    expect(orgCookies.find((c) => c.name === "schakl_auth")?.value).not.toBe(apexAuth?.value);

    // The link is spent: re-opening it (browser history, a shared screen) explains itself
    // instead of silently signing anyone in again.
    expect(handoffUrl).not.toBe("");
    await page.goto(handoffUrl);
    await expect(page.locator('[data-testid="impersonation-handoff-failed"]')).toBeVisible();

    // Stopping takes the operator's footprint on the customer's hostname with it and offers the
    // way back to the console. It lands on this host, not the apex: a form submission that
    // redirects off-origin is blocked by our own `form-action 'self'` CSP.
    await page.goto(orgUrl(ORG.slug) + "/");
    await page.locator('form[action="/impersonation/stop"] button').click();
    await expect(page.locator('[data-testid="impersonation-stopped"]')).toBeVisible();
    const afterStop = (await jar(orgHost)).map((c) => c.name);
    expect(afterStop).not.toContain("schakl_impersonate");
    expect(afterStop).not.toContain("schakl_auth");

    // The way back really is the console — the whole origin, port included, not just the name.
    await page.locator('[data-testid="impersonation-stopped"] a').click();
    await page.waitForURL((url) => url.origin === new URL(APEX).origin);
  });
});
