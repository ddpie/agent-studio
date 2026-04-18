import { test, expect } from "@playwright/test";

test("runtime badge shows live status (not UNKNOWN) and opens drawer", async ({ page }) => {
  await page.goto("/");
  await page.waitForURL(/agents/);

  // Ensure at least one agent row rendered a badge.
  const badge = page.getByTestId("status-badge").first();
  await expect(badge).toBeVisible({ timeout: 15_000 });

  // Assert semantics: the badge must reflect a real AgentCore status,
  // not the UNKNOWN fallback that appears when the API fails / 403s.
  // Sprint 1 regressed once when the CrudHandler role lacked
  // bedrock-agentcore:GetAgentRuntime permission; element visibility alone
  // didn't catch that.
  const readyBadge = page.locator("[data-testid=status-badge][data-status=READY]").first();
  await expect(readyBadge).toBeVisible({ timeout: 30_000 });

  // Drawer opens on click.
  await readyBadge.click();
  await expect(page.getByTestId("runtime-drawer")).toBeVisible();
  await page.getByRole("button", { name: /close|关闭/i }).first().click();
  await expect(page.getByTestId("runtime-drawer")).toBeHidden();
});
