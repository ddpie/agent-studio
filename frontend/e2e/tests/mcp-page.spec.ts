import { test, expect } from "../fixtures/test";

test.describe("MCP Marketplace Page", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/#/mcp");
    await page.waitForLoadState("networkidle");
    // Wait for targets to load
    await page.waitForTimeout(3_000);
  });

  test("page loads with hero, search, and category pills", async ({ page }) => {
    const heading = page.getByRole("heading", { name: /MCP Tools/i });
    await expect(heading).toBeVisible({ timeout: 10_000 });

    // Subtitle with count
    await expect(page.locator("text=/\\d+.*可用|\\d+.*available/")).toBeVisible({ timeout: 10_000 });

    // Search input
    await expect(page.getByPlaceholder(/search|搜索/i)).toBeVisible();

    // Category pills
    await expect(page.getByRole("button", { name: /全部|All/i }).first()).toBeVisible();
  });

  test("displays target cards with categories", async ({ page }) => {
    // Cards should load (each has data-testid="mcp-card-*")
    const cards = page.locator("[data-testid^='mcp-card-']");
    await expect(cards.first()).toBeVisible({ timeout: 10_000 });
    const count = await cards.count();
    expect(count).toBeGreaterThan(0);
  });

  test("category pill filtering works", async ({ page }) => {
    const allCards = page.locator("[data-testid^='mcp-card-']");
    await expect(allCards.first()).toBeVisible({ timeout: 10_000 });
    const totalBefore = await allCards.count();

    // Click a specific category pill
    const securityPill = page.getByRole("button", { name: /安全与合规|Security/i });
    if (await securityPill.isVisible()) {
      await securityPill.click();
      await page.waitForTimeout(500);

      const countAfter = await allCards.count();
      expect(countAfter).toBeLessThan(totalBefore);
      expect(countAfter).toBeGreaterThan(0);

      // Reset
      await page.getByRole("button", { name: /全部|All/i }).first().click();
    }
  });

  test("search filters targets", async ({ page }) => {
    const allCards = page.locator("[data-testid^='mcp-card-']");
    await expect(allCards.first()).toBeVisible({ timeout: 10_000 });
    const totalBefore = await allCards.count();

    await page.getByPlaceholder(/search|搜索/i).fill("cloudwatch");
    await page.waitForTimeout(500);

    const countAfter = await allCards.count();
    expect(countAfter).toBeGreaterThan(0);
    expect(countAfter).toBeLessThan(totalBefore);

    await page.getByPlaceholder(/search|搜索/i).clear();
  });

  test("clicking a card expands inline tool list", async ({ page }) => {
    const cards = page.locator("[data-testid^='mcp-card-']");
    await expect(cards.first()).toBeVisible({ timeout: 10_000 });

    // Click first card
    await cards.first().click();
    await page.waitForTimeout(3_000);

    // Expanded card should show tool items (wrench icons)
    const toolItems = page.locator("svg.lucide-wrench");
    await expect(toolItems.first()).toBeVisible({ timeout: 10_000 });
  });

  test("expanded card shows tool descriptions", async ({ page }) => {
    // Find cloudwatch card specifically
    const cwCard = page.locator("[data-testid='mcp-card-mcp-cloudwatch']");
    if (!(await cwCard.isVisible({ timeout: 5_000 }).catch(() => false))) {
      test.skip(true, "cloudwatch card not available");
      return;
    }

    await cwCard.click();
    await page.waitForTimeout(3_000);

    // Should see tool name in mono font
    const toolName = page.locator(".font-mono").first();
    await expect(toolName).toBeVisible({ timeout: 10_000 });

    // Should see description text
    const descriptions = page.locator("[class*='line-clamp']");
    const descCount = await descriptions.count();
    expect(descCount).toBeGreaterThan(0);
  });

  test("dark mode renders correctly", async ({ page }) => {
    const cards = page.locator("[data-testid^='mcp-card-']");
    await expect(cards.first()).toBeVisible({ timeout: 10_000 });

    // Enable dark mode
    await page.evaluate(() => document.documentElement.classList.add("dark"));
    await page.waitForTimeout(500);

    // Content should still be visible
    await expect(page.getByRole("heading", { name: /MCP Tools/i })).toBeVisible();
    await expect(cards.first()).toBeVisible();
  });
});
