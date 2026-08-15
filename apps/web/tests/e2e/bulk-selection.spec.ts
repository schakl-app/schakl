import { expect, test, type Page } from "@playwright/test";

import { hasCredentials, signIn } from "./auth";

/**
 * The bulk selection, end to end (#330, #331, #332).
 *
 * All three are `DataTable` + `BulkBar` behaviour, so they are exercised on one list — Klanten —
 * and every other list inherits the same components. What each one pins:
 *
 *  - **#330** a tick lives as long as its row is on screen: a page-size change (a re-slice of the
 *    same set) keeps it, a filter that removes the row drops it. The old code emptied the whole
 *    selection on *any* reload of the rows, which is invisible in a functional test that only
 *    ever ticks and posts in one go — hence the size change in the middle.
 *  - **#331** the strip stays on screen while the rows are being picked, and costs a list nobody
 *    is editing nothing at all.
 *  - **#332** a record menu and a selection menu are never offered at once.
 *
 * Selectors are `data-testid` / `name=`, never visible text: the UI ships in Dutch (CLAUDE.md §8)
 * and matching on a string would assert the translation instead of the behaviour.
 *
 * These need a signed-in session (`SCHAKL_E2E_EMAIL` / `SCHAKL_E2E_PASSWORD`) against a stack
 * with at least three clients on it, and skip rather than fail without one.
 */
/**
 * Klanten by default, and any list with a ✎ over `SCHAKL_E2E_LIST` — the nine share the two
 * components under test, so which one runs is a property of the instance's data, not of the
 * behaviour. (It is also the escape hatch when the list you point at happens to be empty.)
 */
const LIST = process.env.SCHAKL_E2E_LIST ?? "/companies?size=25";
const NEEDED = 3;

/**
 * A **data** row, not a section header. A grouped list (contacten by client, taken by status)
 * puts `<tr><th colspan>` rows in the same `<tbody>`, and they hold no cell, no checkbox and no
 * link — a spec that counts them measures the sections instead of the rows.
 */
const DATA_ROW = "table tbody tr:has(td)";

test.describe("bulk selection", () => {
  test.skip(!hasCredentials(), "set SCHAKL_E2E_EMAIL / SCHAKL_E2E_PASSWORD to run");

  /** The selection's count, as a number — the only part of that string a locale does not own. */
  async function count(page: Page): Promise<number> {
    const text = (await page.getByTestId("bulk-count").textContent()) ?? "";
    return Number(text.replace(/\D+/g, ""));
  }

  /** The ids of the ticked rows, read off the link each primary cell carries. */
  async function tickedIds(page: Page): Promise<string[]> {
    return page.evaluate(() =>
      [...document.querySelectorAll("table tbody tr:has(td)")]
        .filter((row) => row.querySelector("input:checked"))
        .map((row) => row.querySelector("a")?.getAttribute("href")?.split("/").pop() ?? ""),
    );
  }

  /**
   * Press the ✎ and wait for it to have taken.
   *
   * Idempotent on purpose: SSR hands over a complete, *unhydrated* page, so a click that lands
   * in that window does nothing at all and a plain click-then-assert is a flake with a story
   * ("the toggle is broken"). This clicks only while the control still says it is off, so the
   * retry can never toggle the mode back shut.
   */
  async function setSelectMode(page: Page, on: boolean): Promise<void> {
    const toggle = page.getByTestId("bulk-toggle");
    await expect(toggle).toBeVisible();
    await expect(async () => {
      if ((await toggle.getAttribute("aria-pressed")) !== String(on)) await toggle.click();
      await expect(toggle).toHaveAttribute("aria-pressed", String(on), { timeout: 1000 });
    }).toPass({ timeout: 20_000 });
  }

  /**
   * Sign in, open the list, and answer how many rows it has.
   *
   * `phone` picks which of the two row renderings to wait for: below `sm` the grid gives way to
   * the concept's own row (docs/UX.md), so a mobile spec that waits for a `<tr>` waits for an
   * element that is deliberately hidden.
   */
  async function openList(page: Page, phone = false): Promise<number> {
    // The suite drives a running stack, and against a dev server the first SSR render of a route
    // is a compile, not a response.
    test.slow();
    await signIn(page);
    await page.goto(LIST);
    const rows = page.locator(phone ? "main ul li" : DATA_ROW);
    await expect(rows.first()).toBeVisible();
    return rows.count();
  }

  test("a page-size change keeps the ticks — the rows never left the screen (#330)", async ({
    page,
  }) => {
    const rows = await openList(page);
    test.skip(rows < NEEDED, `needs ${NEEDED} rows, this instance has ${rows}`);

    await setSelectMode(page, true);
    const boxes = page.getByTestId("row-select");
    for (let i = 0; i < NEEDED; i++) await boxes.nth(i).click();
    expect(await count(page)).toBe(NEEDED);
    const before = await tickedIds(page);

    await page.getByTestId("page-size").selectOption("50");
    await expect(page).toHaveURL(/size=50/);

    // The bug: every one of these was unticked, and the page's own `bulkSelected` was emptied
    // through the binding without the page hearing about it.
    expect(await count(page)).toBe(NEEDED);
    expect(await tickedIds(page)).toEqual(before);
  });

  test("a search drops what vanished and keeps what survived (#330)", async ({ page }) => {
    const rows = await openList(page);
    test.skip(rows < NEEDED, `needs ${NEEDED} rows, this instance has ${rows}`);

    await setSelectMode(page, true);
    const boxes = page.getByTestId("row-select");
    for (let i = 0; i < NEEDED; i++) await boxes.nth(i).click();
    const picked = await tickedIds(page);

    // Search for one ticked row by what its own primary cell says, so at most one of the three
    // can survive. Read from the cell rather than from a link in it: which cell carries the
    // record's link is the list's business, and this spec is about any of them.
    const primary = page.locator(DATA_ROW).first().locator("td").nth(1);
    const term = (await primary.innerText()).trim().split("\n")[0];
    await page.locator("input[name=q], input[type=search]").first().fill(term);
    await expect(page).toHaveURL(/q=/);
    await expect(page.locator(DATA_ROW)).not.toHaveCount(rows);

    const after = await tickedIds(page);
    expect(after.length).toBeLessThan(picked.length);
    // Whatever is still ticked is still on screen: the selection never names a row you cannot see.
    for (const id of after) expect(picked).toContain(id);
    expect(await count(page)).toBe(after.length);
  });

  test("what is posted is what is ticked (#330)", async ({ page }) => {
    const rows = await openList(page);
    test.skip(rows < NEEDED, `needs ${NEEDED} rows, this instance has ${rows}`);

    await setSelectMode(page, true);
    const boxes = page.getByTestId("row-select");
    for (let i = 0; i < NEEDED; i++) await boxes.nth(i).click();
    const picked = await tickedIds(page);

    // Open the delete confirm and read the payload it would post — then leave without posting.
    // The labels can be right while the ids are wrong, which is the failure this catches.
    await page.getByTestId("bulk-bar").locator("button").last().click();
    const ids = page.locator("dialog[open] input[name=ids], [role=dialog] input[name=ids]");
    await expect(ids).toHaveCount(1);
    expect((await ids.inputValue()).split(",")).toEqual(picked);
  });

  test("the ✕ drops the selection and the ✎ leaves the mode (#330)", async ({ page }) => {
    const rows = await openList(page);
    test.skip(rows < 1, "needs at least one row");

    await setSelectMode(page, true);
    await page.getByTestId("row-select").first().click();
    expect(await count(page)).toBe(1);

    await page.getByTestId("bulk-clear").click();
    expect(await count(page)).toBe(0);
    await expect(page.getByTestId("bulk-bar")).toBeVisible(); // cleared, not closed

    await page.getByTestId("row-select").first().click();
    await setSelectMode(page, false);
    await expect(page.getByTestId("bulk-bar")).toHaveCount(0);
    await expect(page.getByTestId("row-select")).toHaveCount(0);
  });

  /** A bar cannot be *stuck* on a page that does not scroll, so a short list proves nothing. */
  async function scrollable(page: Page): Promise<boolean> {
    return page.evaluate(() => document.documentElement.scrollHeight > window.innerHeight + 200);
  }

  test("the strip stays on screen while rows are being picked (#331)", async ({ page }) => {
    const rows = await openList(page);
    test.skip(rows < NEEDED, `needs ${NEEDED} rows, this instance has ${rows}`);
    test.skip(!(await scrollable(page)), "needs a list long enough to scroll");

    const firstRowBefore = await page.locator(DATA_ROW).first().boundingBox();
    await setSelectMode(page, true);
    const bar = page.getByTestId("bulk-bar");

    await page.mouse.wheel(0, 4000);
    await page.waitForTimeout(200);
    // `toBeVisible()` passes for an element scrolled far off screen; this is the assertion.
    await expect(bar).toBeInViewport();
    const box = await bar.boundingBox();
    expect(box!.y).toBeLessThan(4); // stuck to the top, not merely tall

    // A row ticked far down the list updates the stuck strip without scrolling back up.
    await page.getByTestId("row-select").last().click();
    expect(await count(page)).toBe(1);
    await expect(bar).toBeInViewport();

    // And a list nobody is editing is exactly as tall as it was.
    await page.mouse.wheel(0, -4000);
    await setSelectMode(page, false);
    await expect(bar).toHaveCount(0);
    const firstRowAfter = await page.locator(DATA_ROW).first().boundingBox();
    expect(Math.round(firstRowAfter!.y)).toBe(Math.round(firstRowBefore!.y));
  });

  test("the strip sticks on a phone too, one row of content tall (#331)", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 740 });
    const rows = await openList(page, true);
    test.skip(rows < 1, "needs at least one row");

    test.skip(!(await scrollable(page)), "needs a list long enough to scroll");

    await setSelectMode(page, true);
    const bar = page.getByTestId("bulk-bar");
    await page.mouse.wheel(0, 2000);
    await page.waitForTimeout(200);
    await expect(bar).toBeInViewport();

    const box = await bar.boundingBox();
    expect(box!.y).toBeLessThan(4);
    // Stuck to the top of a small screen, a wrapping strip eats a third of it; this one does not.
    expect(box!.height).toBeLessThan(740 / 4);
  });

  test("a record menu and a selection are never offered at once (#332)", async ({ page }) => {
    const rows = await openList(page);
    test.skip(rows < 1, "needs at least one row");

    const menus = page.locator("table [data-actions-menu]");
    const withoutSelection = await menus.count();
    test.skip(withoutSelection === 0, "this account has no record actions on this list");

    await setSelectMode(page, true);
    // Two controls that look alike must not have different scopes: while the bar's Verwijderen
    // means the ticked rows, no row may offer a Verwijderen that means one.
    await expect(menus).toHaveCount(0);

    await setSelectMode(page, false);
    await expect(menus).toHaveCount(withoutSelection);
  });

  test("the phone row's ⋯ goes with them (#332)", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 740 });
    await openList(page, true);
    const menus = page.locator("main ul li [data-actions-menu]");
    await expect(menus.first()).toBeVisible();
    const before = await menus.count();

    await setSelectMode(page, true);
    // The phone row is the page's own snippet, so this is hidden by `DataTable`'s one CSS rule
    // rather than by nine `{#if}`s — it stays in the DOM and must not be visible.
    await expect(menus.first()).toBeHidden();

    await setSelectMode(page, false);
    await expect(menus.first()).toBeVisible();
    expect(await menus.count()).toBe(before);
  });
});
