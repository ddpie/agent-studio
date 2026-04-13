import { test, expect } from "../fixtures/test";

test.describe("MCP Page", () => {
  test("MCP page loads and displays content", async ({ page }) => {
    await page.goto("/#/mcp");
    await page.waitForLoadState("networkidle");

    // Page heading should be visible
    const heading = page.locator("h2").first();
    await expect(heading).toBeVisible({ timeout: 10_000 });

    // Should show either gateway list or empty state
    await page.waitForTimeout(2_000);
    const hasContent = await page.locator(".flex-1").first().isVisible();
    expect(hasContent).toBe(true);
  });
});
