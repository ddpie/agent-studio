import { test, expect } from "@playwright/test";

test("evaluations tab renders (empty state or scores)", async ({ page }) => {
  await page.goto("/");
  await page.waitForURL(/agents/);

  await expect(page.getByTestId("status-badge").first()).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: /^(Edit|编辑)$/ }).first().click();

  await expect(page.getByTestId("evaluations-tab")).toBeVisible({ timeout: 15_000 });

  // Either the empty-state copy OR the score table should be visible —
  // both are legitimate for a freshly-deployed eval config.
  const emptyTitle = page.getByTestId("evaluations-tab").getByText(/No evaluations yet|暂无评估结果/);
  const table = page.getByTestId("evaluations-table");
  await expect(emptyTitle.or(table)).toBeVisible();
});

test("evaluator rows display numeric latest score when present", async ({ page }) => {
  await page.goto("/");
  await page.waitForURL(/agents/);
  await expect(page.getByTestId("status-badge").first()).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: /^(Edit|编辑)$/ }).first().click();
  await expect(page.getByTestId("evaluations-tab")).toBeVisible({ timeout: 15_000 });

  const table = page.getByTestId("evaluations-table");
  if (await table.isVisible().catch(() => false)) {
    const firstScore = page.locator("[data-testid^='evaluator-row-'] [data-score]").first();
    const score = await firstScore.getAttribute("data-score");
    expect(Number(score)).toBeGreaterThanOrEqual(0);
    expect(Number(score)).toBeLessThanOrEqual(1);
  } else {
    test.info().annotations.push({ type: "skip", description: "no evaluations yet in this env" });
  }
});
