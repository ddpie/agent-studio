import { test as setup, expect } from "@playwright/test";

setup("authenticate", async ({ page }) => {
  await page.goto("/");

  // Amplify Authenticator renders username + password fields
  const username = page.getByRole("textbox", { name: /username|email/i });
  const password = page.locator('input[type="password"]');
  const signIn = page.getByRole("button", { name: /sign in/i });

  await expect(username).toBeVisible({ timeout: 15_000 });

  await username.fill(process.env.E2E_USERNAME!);
  await password.fill(process.env.E2E_PASSWORD!);
  await signIn.click();

  // Wait for the app to fully load — the sidebar nav appears when auth + workspace are ready
  await page.waitForSelector('a[href*="#/agents"], a[href*="#/skills"]', {
    state: "visible",
    timeout: 30_000,
  });

  // Save auth state for reuse
  await page.context().storageState({ path: ".auth/user.json" });
});
