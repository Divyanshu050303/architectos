import { expect, test } from "@playwright/test";

/**
 * Sign-in round trip against the mock backend, which starts signed in as a demo user.
 * The provider hop is simulated: "Continue with …" signs in to the mock and lands on /auth/callback.
 */
test("sign out, get sent to /login from a deep link, sign in with GitHub and land back there", async ({
  page,
}) => {
  await page.goto("/dashboard");
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();

  await page.getByRole("button", { name: "Account" }).click();
  await expect(page.getByText("demo@architectos.dev")).toBeVisible();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);

  await page.goto("/projects");
  await expect(page).toHaveURL(/\/login\?next=%2Fprojects$/);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Welcome back. Sign in.");

  await page.getByRole("button", { name: "Continue with GitHub" }).click();
  await expect(page).toHaveURL(/\/projects$/);
});

test("sign-up links back to sign-in and keeps the destination", async ({ page }) => {
  await page.goto("/signup?next=%2Fprojects");
  await expect(page.getByRole("button", { name: "Continue with Google" })).toBeVisible();
  await page.getByRole("link", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(/\/login\?next=%2Fprojects$/);
});

test("a cancelled provider sign-in returns to /login with an explanation", async ({ page }) => {
  await page.goto("/auth/callback?error=access_denied&next=%2Fprojects");
  await expect(page).toHaveURL(/\/login\?error=access_denied&next=%2Fprojects$/);
  await expect(page.getByText(/cancelled at the provider/)).toBeVisible();
});

test("create an account with email, sign out and sign back in with the password", async ({ page }) => {
  await page.goto("/signup?next=%2Fprojects");
  await page.getByLabel("Name").fill("Ada Lovelace");
  await page.getByLabel("Email").fill("ada@example.com");
  await page.getByLabel("Password", { exact: true }).fill("analytical engine");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/projects$/);

  await page.getByRole("button", { name: "Account" }).click();
  await expect(page.getByText("ada@example.com")).toBeVisible();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);

  await page.getByLabel("Email").fill("ada@example.com");
  await page.getByLabel("Password", { exact: true }).fill("wrong password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByText("Email or password is incorrect.")).toBeVisible();

  await page.getByLabel("Password", { exact: true }).fill("analytical engine");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
});

test("forgot password, reset it from the emailed link and sign in with the new one", async ({ page }) => {
  await page.goto("/login");
  await page.getByRole("link", { name: "Forgot password?" }).click();
  // Client-side navigation: wait for the new page, or the fill lands in the sign-in form's Email.
  await expect(page.getByRole("heading", { name: "Forgot your password?" })).toBeVisible();
  await page.getByLabel("Email").fill("demo@architectos.dev");
  await page.getByRole("button", { name: "Send reset link" }).click();
  await expect(page.getByText(/If an account exists for demo@architectos.dev/)).toBeVisible();

  // The mock sends no email; this is the link it would contain.
  await page.goto("/reset-password?token=mock-reset-token");
  await page.getByLabel("New password", { exact: true }).fill("a brand new password");
  await page.getByLabel("Confirm new password").fill("a brand new password");
  await page.getByRole("button", { name: "Set new password" }).click();
  await expect(page).toHaveURL(/\/login\?reset=done$/);
  await expect(page.getByText("Your password was changed.", { exact: false })).toBeVisible();

  await page.getByLabel("Email").fill("demo@architectos.dev");
  await page.getByLabel("Password", { exact: true }).fill("a brand new password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
});
