import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";

/**
 * Golden path (spec §79), run against the labelled mock backend (NEXT_PUBLIC_API_MOCKS=true):
 * create project → requirements → generate → architecture → select PostgreSQL → inspect capacity
 * → run validation → view finding → run capacity analysis → operating envelope.
 */

async function expectNoAxeViolations(page: Page, context: string) {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  const summary = results.violations.map((v) => `${v.id} (${v.nodes.length}): ${v.help}`);
  expect(summary, `axe violations on ${context}`).toEqual([]);
}

const sidebar = (page: Page) => page.getByRole("navigation", { name: "Project" });

test("golden path: requirements to operating envelope", async ({ page }) => {
  await page.goto("/dashboard");
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
  await expectNoAxeViolations(page, "dashboard");

  // Create project
  await page.getByRole("button", { name: "New system" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Create system" });
  await dialog.getByLabel("Name").fill("Checkout Platform");
  await dialog.getByRole("button", { name: "Create system" }).click();
  await expect(page).toHaveURL(/\/project\/[^/]+\/requirements$/);

  // Enter requirements
  await page
    .getByLabel("Description")
    .fill("Checkout for an online store. Read-heavy catalogue with a cache, order events processed async.");
  await page.getByLabel("Daily active users").fill("1000000");
  await page.getByLabel("Peak RPS").fill("12000");
  await page.getByRole("button", { name: "Save requirements" }).click();

  // Generate architecture (explicit progress, then the workspace)
  await page.getByRole("button", { name: "Generate architecture" }).click();
  await expect(page.getByText("Analyzing requirements")).toBeVisible();
  await expect(page).toHaveURL(/\/architecture/, { timeout: 20_000 });

  // View architecture and select PostgreSQL
  await expect(page.getByLabel("Architecture canvas")).toBeVisible();
  await page.locator(".react-flow__node").filter({ hasText: "PostgreSQL" }).first().click();
  const inspector = page.getByRole("tablist", { name: "Component details" });
  await expect(inspector).toBeVisible();
  await expect(page).toHaveURL(/node=/);

  // Inspect capacity
  await inspector.getByRole("tab", { name: "Capacity" }).click();
  await expect(page.getByRole("tabpanel")).toContainText(/connections|reads|writes|cpu/i);
  await expectNoAxeViolations(page, "architecture workspace");

  // Run validation and view a finding
  await sidebar(page).getByRole("link", { name: "Validation", exact: true }).click();
  await page.getByRole("button", { name: /Run validation|Re-run validation/ }).click();
  await expect(page.getByText("Why it matters").first()).toBeVisible({ timeout: 15_000 });
  await expectNoAxeViolations(page, "validation");

  // Run capacity analysis and view the operating envelope
  await sidebar(page).getByRole("link", { name: "Capacity", exact: true }).click();
  await page.getByRole("button", { name: /Run capacity analysis|Re-run analysis/ }).click();
  await expect(page.getByRole("heading", { name: "Operating envelope" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("img", { name: /Operating envelope/ })).toBeVisible();
  await expectNoAxeViolations(page, "capacity");
});

test("AI change is a proposal that requires approval", async ({ page }) => {
  await page.goto("/project/proj_food/architecture");
  const prompt = page.getByRole("textbox", { name: /Ask ArchitectOS|architecture change/i });
  await prompt.fill("Add Redis caching");
  await prompt.press(process.platform === "darwin" ? "Meta+Enter" : "Control+Enter");

  // A diff is shown; nothing is applied until the user approves.
  await expect(page.getByText(/recommendation/i).first()).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("Saved v3")).toBeVisible();
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(page.getByText("Saved v4")).toBeVisible({ timeout: 15_000 });
});
