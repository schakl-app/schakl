import type { Page } from "@playwright/test";

/**
 * Signing in, for the specs that cannot say anything without a session.
 *
 * The credentials come from the environment and nothing is committed: an instance is somebody's
 * own dev stack, and a fixture user hard-coded here would either not exist (every run red) or
 * would be an account this repository told the world the password of.
 *
 * `hasSession()` is what the specs skip on, so a developer who has not set them — and CI, which
 * has no seeded stack at all — gets a skip rather than a failure. `smoke.spec.ts` makes the same
 * bargain from the other side: it asserts only what an instance with no data can answer.
 */
export const E2E_EMAIL = process.env.SCHAKL_E2E_EMAIL;
export const E2E_PASSWORD = process.env.SCHAKL_E2E_PASSWORD;

export function hasCredentials(): boolean {
  return !!E2E_EMAIL && !!E2E_PASSWORD;
}

/**
 * Post the login action directly rather than driving the form.
 *
 * The form is `smoke.spec.ts`'s subject and is asserted there; here a session is a *precondition*,
 * and a precondition that types into two fields is a second place for an unrelated UI change to
 * fail this file. Selectors stay `name=` / `data-testid` everywhere for the same reason the app's
 * own rule says so: the UI ships in Dutch (CLAUDE.md §8), so matching visible text asserts the
 * translation instead of the behaviour.
 */
export async function signIn(page: Page): Promise<void> {
  await page.goto("/login");
  const result = await page.evaluate(
    async ([email, password]) => {
      const response = await fetch("/login?/login", {
        method: "POST",
        headers: { "x-sveltekit-action": "true" },
        body: new URLSearchParams({ email: email!, password: password! }),
      });
      return { status: response.status, body: await response.text() };
    },
    [E2E_EMAIL, E2E_PASSWORD],
  );
  if (!result.body.includes("redirect")) {
    throw new Error(`sign-in failed (${result.status}): ${result.body.slice(0, 200)}`);
  }
}
