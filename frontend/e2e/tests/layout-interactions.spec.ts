import { test, expect } from "../fixtures/test";

test.describe("Layout Interactions", () => {
  test("agents sidebar collapse on double-click divider", async ({ page }) => {
    await page.goto("/#/agents");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2_000);

    // The divider is a 1px wide element with cursor-col-resize
    const divider = page.locator(".cursor-col-resize").first();
    await expect(divider).toBeVisible();

    // Get initial sidebar width
    const sidebar = page.locator("aside").first();
    const initialWidth = await sidebar.evaluate(el => el.getBoundingClientRect().width);
    expect(initialWidth).toBeGreaterThan(100);

    // Double-click to collapse
    await divider.dblclick();
    await page.waitForTimeout(500);

    // Sidebar should be collapsed (width ~56px)
    const collapsedWidth = await sidebar.evaluate(el => el.getBoundingClientRect().width);
    expect(collapsedWidth).toBeLessThan(100);

    // Double-click again to expand
    await divider.dblclick();
    await page.waitForTimeout(500);

    // Sidebar should be expanded again
    const expandedWidth = await sidebar.evaluate(el => el.getBoundingClientRect().width);
    expect(expandedWidth).toBeGreaterThan(100);
  });

  test("collapsed sidebar shows icon-only agent buttons", async ({ page }) => {
    await page.goto("/#/agents");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2_000);

    // Collapse sidebar
    const divider = page.locator(".cursor-col-resize").first();
    await divider.dblclick();
    await page.waitForTimeout(500);

    // In collapsed mode, Meta-Agent should show as an icon button (MessageSquare)
    const metaBtn = page.locator("button").filter({ has: page.locator("svg.lucide-message-square") }).first();
    await expect(metaBtn).toBeVisible();

    // Expand back
    await divider.dblclick();
    await page.waitForTimeout(500);
  });

  test("sign out button is visible in header", async ({ page }) => {
    await page.goto("/#/agents");
    await page.waitForLoadState("networkidle");

    // Sign out button should be in the header
    const signOutBtn = page.getByRole("button", { name: /sign out|退出|登出/i });
    await expect(signOutBtn).toBeVisible({ timeout: 10_000 });
  });
});
