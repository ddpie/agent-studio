import { test, expect } from "@playwright/test";

test("A2A banner renders on Meta-Agent chat", async ({ page }) => {
  await page.goto("/");
  // Root / = Meta-Agent chat (no agentId)
  const banner = page.getByTestId("a2a-banner");
  await expect(banner).toBeVisible({ timeout: 20_000 });
  const url = await banner.getAttribute("data-url");
  expect(url).toMatch(/^https:\/\//);
  const arn = await banner.getAttribute("data-arn");
  expect(arn).toContain(":runtime/");
});

test("viewing agent card JSON works", async ({ page }) => {
  await page.goto("/");
  const banner = page.getByTestId("a2a-banner");
  await expect(banner).toBeVisible({ timeout: 20_000 });
  await banner.getByTestId("a2a-view-card").click();
  await expect(page.getByTestId("a2a-card-json")).toBeVisible();
  const txt = await page.getByTestId("a2a-card-json").innerText();
  const parsed = JSON.parse(txt);
  expect(parsed.name).toBeTruthy();
  expect(parsed.url).toMatch(/^https:\/\//);
  expect(Array.isArray(parsed.skills)).toBe(true);
});

test("copy endpoint button copies URL to clipboard", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.goto("/");
  const banner = page.getByTestId("a2a-banner");
  await expect(banner).toBeVisible({ timeout: 20_000 });
  const url = await banner.getAttribute("data-url");
  await banner.getByTestId("a2a-copy-endpoint").click();
  const clip = await page.evaluate(() => navigator.clipboard.readText());
  expect(clip).toBe(url);
});
