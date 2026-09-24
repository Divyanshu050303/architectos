import { expect, type Page, test } from "@playwright/test";

/**
 * Visual regression (spec §81) against the seeded mock project. Baselines live next to
 * this file; regenerate intentionally with `npx playwright test --update-snapshots`.
 */

const SCREENS = [
  { name: "dashboard", path: "/dashboard" },
  { name: "architecture-workspace", path: "/project/proj_food/architecture" },
  { name: "component-inspector", path: "/project/proj_food/architecture?node=postgres&mode=capacity" },
  { name: "validation", path: "/project/proj_food/validation" },
  { name: "capacity", path: "/project/proj_food/capacity" },
  { name: "simulation", path: "/project/proj_food/simulation" },
  { name: "reliability", path: "/project/proj_food/reliability" },
  { name: "cost", path: "/project/proj_food/cost" },
  { name: "evolution", path: "/project/proj_food/evolution" },
];

async function settle(page: Page, path: string) {
  await page.goto(path);
  await page.waitForLoadState("networkidle");
  await expect(page.locator(".animate-pulse")).toHaveCount(0);
  // Let React Flow finish fitting the view.
  await page.waitForTimeout(600);
}

for (const screen of SCREENS) {
  test(`visual: ${screen.name}`, async ({ page }) => {
    await settle(page, screen.path);
    await expect(page).toHaveScreenshot(`${screen.name}.png`, { mask: [page.locator("time")] });
  });
}

test("visual: architecture workspace (dark)", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await settle(page, "/project/proj_food/architecture");
  await expect(page).toHaveScreenshot("architecture-workspace-dark.png", { mask: [page.locator("time")] });
});
