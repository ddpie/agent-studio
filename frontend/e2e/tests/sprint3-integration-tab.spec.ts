import { test, expect } from "@playwright/test";

test("Integration section shows card URL and generate-key button", async ({ page }) => {
  await page.goto("/");
  await page.waitForURL(/agents/);
  await expect(page.getByTestId("status-badge").first()).toBeVisible({ timeout: 15_000 });
  await page.locator("[data-testid^='view-agent-']").first().click();
  await expect(page.getByTestId("agent-detail-title")).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("integration-section").scrollIntoViewIfNeeded();
  await expect(page.getByTestId("integration-tab")).toBeVisible({ timeout: 15_000 });

  const cardUrl = await page.getByTestId("card-url-value").textContent();
  expect(cardUrl).toContain("/.well-known/agent-card.json");

  const endpointUrl = await page.getByTestId("endpoint-url-value").textContent();
  expect(endpointUrl).toContain("/a2a/agents/");

  await expect(page.getByTestId("generate-key-btn")).toBeVisible();
});

test("generate + revoke key flow", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.goto("/");
  await page.waitForURL(/agents/);
  await page.locator("[data-testid^='view-agent-']").first().click();
  await expect(page.getByTestId("agent-detail-title")).toBeVisible({ timeout: 15_000 });
  await page.getByTestId("integration-section").scrollIntoViewIfNeeded();

  await page.getByTestId("generate-key-btn").click();
  await expect(page.getByTestId("new-key-modal")).toBeVisible({ timeout: 15_000 });
  const keyText = await page.getByTestId("new-key-value").textContent();
  expect(keyText).toMatch(/^as_[A-Za-z0-9]{32}$/);

  await page.getByTestId("new-key-close").click();
  await expect(page.getByTestId("new-key-modal")).not.toBeVisible({ timeout: 5_000 });

  await expect(page.getByTestId("a2a-keys-table")).toBeVisible();
  const keyRow = page.locator("[data-testid^='a2a-key-row-']").first();
  await expect(keyRow).toBeVisible();

  page.once("dialog", (d) => d.accept());
  await keyRow.locator("[data-testid^='revoke-key-']").click();

  await expect(keyRow).not.toBeVisible({ timeout: 15_000 });
});
