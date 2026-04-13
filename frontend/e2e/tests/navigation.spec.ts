import { test, expect } from "../fixtures/test";

test.describe("Navigation & Settings", () => {
  test("sidebar navigation switches pages correctly", async ({ page }) => {
    await page.goto("/#/agents");
    await page.waitForLoadState("networkidle");

    // Navigate to Skills
    await page.locator('a[href*="#/skills"]').click();
    await expect(page).toHaveURL(/#\/skills/);

    // Navigate to Tools
    await page.locator('a[href*="#/tools"]').click();
    await expect(page).toHaveURL(/#\/tools/);

    // Navigate to MCP
    await page.locator('a[href*="#/mcp"]').click();
    await expect(page).toHaveURL(/#\/mcp/);

    // Navigate to Settings
    await page.locator('a[href*="#/settings"]').click();
    await expect(page).toHaveURL(/#\/settings/);

    // Navigate back to Agents
    await page.locator('a[href*="#/agents"]').click();
    await expect(page).toHaveURL(/#\/agents/);
  });

  test("theme switching toggles dark class", async ({ page }) => {
    await page.goto("/#/settings");
    await page.waitForLoadState("networkidle");

    // Click Dark mode button
    const darkButton = page.getByRole("button", { name: /dark|深色/i });
    await darkButton.click();
    await expect(page.locator("html")).toHaveClass(/dark/);

    // Click Light mode button
    const lightButton = page.getByRole("button", { name: /light|浅色/i });
    await lightButton.click();
    // html should NOT have dark class
    await expect(page.locator("html")).not.toHaveClass(/dark/);
  });

  test("language switching changes UI text", async ({ page }) => {
    await page.goto("/#/settings");
    await page.waitForLoadState("networkidle");

    // Switch to English
    await page.getByRole("button", { name: "English" }).click();
    await page.waitForTimeout(500);
    // Settings heading should be in English
    await expect(page.locator("h2").first()).toContainText("Settings");

    // Switch to Chinese
    await page.getByRole("button", { name: "中文" }).click();
    await page.waitForTimeout(500);
    // Settings heading should be in Chinese
    await expect(page.locator("h2").first()).toContainText("设置");
  });

  test("settings — reset sidebar width", async ({ page }) => {
    await page.goto("/#/settings");
    await page.waitForLoadState("networkidle");

    const resetButtons = page.getByRole("button", { name: /reset|恢复默认/i });
    const firstReset = resetButtons.first();
    await expect(firstReset).toBeVisible();
    await firstReset.click();
    await page.waitForTimeout(300);
    await expect(page.getByText("224")).toBeVisible();
  });

  test("settings — infrastructure info displays", async ({ page }) => {
    await page.goto("/#/settings");
    await page.waitForLoadState("networkidle");

    await expect(page.getByText("S3 + DynamoDB")).toBeVisible();
    await expect(page.getByText("Cognito", { exact: true })).toBeVisible();
  });

  test("settings — export and clear buttons exist", async ({ page }) => {
    await page.goto("/#/settings");
    await page.waitForLoadState("networkidle");

    const exportBtn = page.getByRole("button", { name: /export|导出/i });
    await expect(exportBtn).toBeVisible();

    const clearBtn = page.getByRole("button", { name: /clear|清除/i });
    await expect(clearBtn).toBeVisible();
  });
});
