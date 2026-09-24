import { expect, type Locator, type Page, test } from "@playwright/test";

/**
 * Keyboard accessibility (spec §60, §62, §80), against the labelled mock backend:
 * Tab order reaches the landmarks, the skip link works, dialogs trap focus and give it back
 * on Esc, and the canvas shortcuts (F, Esc) work.
 */

const PROJECT = "/project/proj_food";

/** Landmark that contains the focused element: "skip", "banner", "navigation", "main" or "other". */
async function focusedRegion(page: Page): Promise<string> {
  return page.evaluate(() => {
    const el = document.activeElement;
    if (!el || el === document.body) return "none";
    if (el.textContent?.trim() === "Skip to content") return "skip";
    if (el.closest("main")) return "main";
    if (el.closest("nav")) return "navigation";
    if (el.closest("header")) return "banner";
    return "other";
  });
}

async function focusIsInside(locator: Locator): Promise<boolean> {
  return locator.evaluate((el) => el.contains(document.activeElement));
}

/** Tab forwards and backwards; focus must never leave `container`. */
async function expectFocusTrapped(page: Page, container: Locator, presses = 12) {
  for (let i = 0; i < presses; i++) {
    await page.keyboard.press("Tab");
    expect(await focusIsInside(container), `focus escaped on Tab #${i + 1}`).toBe(true);
  }
  for (let i = 0; i < presses; i++) {
    await page.keyboard.press("Shift+Tab");
    expect(await focusIsInside(container), `focus escaped on Shift+Tab #${i + 1}`).toBe(true);
  }
}

/** The architecture page has no h1: wait for the canvas instead. */
async function settleCanvas(page: Page, path: string) {
  await page.goto(path);
  await expect(page.locator(".react-flow__node").first()).toBeVisible({ timeout: 20_000 });
  await page.waitForLoadState("networkidle");
}

async function settle(page: Page, path: string) {
  await page.goto(path);
  await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible({ timeout: 20_000 });
  await page.waitForLoadState("networkidle");
}

test.describe("keyboard: landmarks and skip link", () => {
  test("Tab order starts with the skip link and reaches banner, navigation and main in order", async ({
    page,
  }) => {
    await settle(page, `${PROJECT}/capacity`);
    const firstSeen = new Map<string, number>();
    for (let i = 0; i < 80 && !firstSeen.has("main"); i++) {
      await page.keyboard.press("Tab");
      const region = await focusedRegion(page);
      if (!firstSeen.has(region)) firstSeen.set(region, i);
    }
    expect(firstSeen.get("skip")).toBe(0);
    for (const region of ["banner", "navigation", "main"]) {
      expect(firstSeen.has(region), `Tab never reached ${region}`).toBe(true);
    }
    expect(firstSeen.get("banner") ?? Infinity).toBeLessThan(firstSeen.get("navigation") ?? -1);
    expect(firstSeen.get("navigation") ?? Infinity).toBeLessThan(firstSeen.get("main") ?? -1);
  });

  for (const path of ["/dashboard", `${PROJECT}/capacity`]) {
    test(`skip link moves keyboard focus into main (${path})`, async ({ page }) => {
      await settle(page, path);
      await page.keyboard.press("Tab");
      const skip = page.getByRole("link", { name: "Skip to content" });
      await expect(skip).toBeFocused();
      await expect(skip).toBeVisible();

      await page.keyboard.press("Enter");
      await expect(page).toHaveURL(/#main$/);
      await page.keyboard.press("Tab");
      expect(await focusedRegion(page)).toBe("main");
    });
  }
});

test.describe("keyboard: dialogs trap focus and restore it on Esc", () => {
  test("Create system", async ({ page }) => {
    await settle(page, "/dashboard");
    const trigger = page.getByRole("button", { name: "New system" }).first();
    await trigger.focus();
    await page.keyboard.press("Enter");

    const dialog = page.getByRole("dialog", { name: "Create system" });
    await expect(dialog).toBeVisible();
    expect(await focusIsInside(dialog)).toBe(true);
    await expectFocusTrapped(page, dialog);

    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(trigger).toBeFocused();
  });

  test("Command palette", async ({ page }) => {
    await settle(page, `${PROJECT}/capacity`);
    const origin = page
      .getByRole("navigation", { name: "Project" })
      .getByRole("link", { name: "Validation" });
    await origin.focus();
    await page.keyboard.press("ControlOrMeta+k");

    const palette = page.getByRole("dialog", { name: "Command palette" });
    await expect(palette).toBeVisible();
    await expect(palette.getByRole("combobox", { name: "Search commands" })).toBeFocused();
    await expectFocusTrapped(page, palette, 4);

    // The project-wide actions are registered on every project page (spec §61).
    await page.keyboard.type("generate architecture");
    await expect(palette.getByRole("option", { name: /Generate architecture/ })).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(palette).toBeHidden();
    await expect(origin).toBeFocused();
  });

  test("Compare versions", async ({ page }) => {
    await settleCanvas(page, `${PROJECT}/architecture`);
    const trigger = page.getByRole("button", { name: "Architecture versions" });
    await trigger.focus();
    await page.keyboard.press("Enter");
    const menu = page.getByRole("menu");
    await expect(menu).toBeVisible();
    await menu.getByRole("menuitem", { name: "Compare with…", exact: true }).focus();
    await page.keyboard.press("Enter");

    const dialog = page.getByRole("dialog", { name: "Compare versions" });
    await expect(dialog).toBeVisible();
    expect(await focusIsInside(dialog)).toBe(true);
    await expectFocusTrapped(page, dialog);

    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(trigger).toBeFocused();
  });
});

test.describe("keyboard: canvas shortcuts", () => {
  const viewportTransform = (page: Page) =>
    page.locator(".react-flow__viewport").evaluate((el) => getComputedStyle(el).transform);
  const scaleOf = (matrix: string) => Number(/matrix\(([^,]+)/.exec(matrix)?.[1] ?? "NaN");

  test("F fits the architecture after zooming away", async ({ page }) => {
    await settleCanvas(page, `${PROJECT}/architecture`);
    await page.waitForTimeout(600); // initial fit
    const fitted = await viewportTransform(page);

    const canvas = page.getByLabel("Architecture canvas");
    const box = await canvas.boundingBox();
    if (!box) throw new Error("canvas has no box");
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.wheel(0, 600);
    await expect.poll(() => viewportTransform(page)).not.toBe(fitted);
    const moved = await viewportTransform(page);

    // Shortcuts are ignored while typing; make sure focus is on the page, not an input.
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
    await page.keyboard.press("f");
    await expect.poll(() => viewportTransform(page)).not.toBe(moved);
    await expect
      .poll(async () => Math.abs(scaleOf(await viewportTransform(page)) - scaleOf(fitted)))
      .toBeLessThan(0.05 * scaleOf(fitted));
  });

  test("Esc closes the inspector", async ({ page }) => {
    await settleCanvas(page, `${PROJECT}/architecture`);
    await page.locator(".react-flow__node").filter({ hasText: "PostgreSQL" }).first().click();
    const inspector = page.getByRole("tablist", { name: "Component details" });
    await expect(inspector).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(inspector).toBeHidden();
  });
});
