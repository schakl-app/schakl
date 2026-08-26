import { expect, test } from "@playwright/test";

import { hasCredentials, signIn } from "./auth";

/**
 * Instellingen → Projecten: the budget alert setting, end to end.
 *
 * Pins the round trip the unit layer cannot: the screen loads the org's stored values, a save
 * posts the checkbox by presence and the threshold as a number, and a reload shows what was
 * stored rather than what the form defaulted to. The values are restored afterwards, because
 * the stack is somebody's dev instance and the nightly watch reads this row.
 *
 * Selectors are `name=` / `id=`, never visible text: the UI ships in Dutch (CLAUDE.md §8) and
 * matching on a string would assert the translation instead of the behaviour.
 *
 * Needs a signed-in admin session (`SCHAKL_E2E_EMAIL` / `SCHAKL_E2E_PASSWORD`); skips without.
 */
test.describe("projects settings", () => {
  test.skip(!hasCredentials(), "set SCHAKL_E2E_EMAIL / SCHAKL_E2E_PASSWORD to run");

  test("threshold and mail toggle survive a save and a reload", async ({ page }) => {
    await signIn(page);
    await page.goto("/settings/projects");

    const threshold = page.locator("input[name=budget_alert_threshold]");
    await expect(threshold).toBeVisible();
    const before = {
      threshold: await threshold.inputValue(),
      emails: await page.locator("input[name=budget_alert_emails]").isChecked(),
    };

    // Save a different threshold and flip the mail toggle.
    const nextValue = before.threshold === "80" ? "85" : "80";
    await threshold.fill(nextValue);
    await page.locator("input[name=budget_alert_emails]").setChecked(!before.emails);
    await page.locator("form button[type=submit]").click();
    await expect(page.locator("form p.text-sm.text-green-600")).toBeVisible();

    // A reload shows the stored row, not the form's defaults.
    await page.reload();
    await expect(threshold).toHaveValue(nextValue);
    expect(await page.locator("input[name=budget_alert_emails]").isChecked()).toBe(
      !before.emails,
    );

    // Leave the instance as found.
    await threshold.fill(before.threshold);
    await page.locator("input[name=budget_alert_emails]").setChecked(before.emails);
    await page.locator("form button[type=submit]").click();
    await expect(page.locator("form p.text-sm.text-green-600")).toBeVisible();
  });
});
