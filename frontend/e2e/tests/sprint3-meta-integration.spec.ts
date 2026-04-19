import { test, expect } from "@playwright/test";

test("Meta-Agent A2A modal opens from chat header", async ({ page }) => {
  await page.goto("/");
  await page.waitForURL(/agents/);

  const btn = page.getByTestId("meta-a2a-open");
  await expect(btn).toBeVisible({ timeout: 15_000 });
  await btn.click();

  await expect(page.getByTestId("integration-tab")).toBeVisible({ timeout: 15_000 });
  const cardUrl = await page.getByTestId("card-url-value").textContent();
  expect(cardUrl).toContain("/a2a/meta-agent/.well-known/agent-card.json");

  const endpointUrl = await page.getByTestId("endpoint-url-value").textContent();
  expect(endpointUrl).toContain("/a2a/meta-agent");
});
