import { test, expect } from "@playwright/test";

async function openFirstAgent(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.waitForURL(/agents/);
  await expect(page.getByTestId("status-badge").first()).toBeVisible({ timeout: 15_000 });
  const viewBtn = page.locator("[data-testid^='view-agent-']").first();
  await viewBtn.click();
  await page.waitForURL(/#\/agents\/[^/]+$/);
  await expect(page.getByTestId("agent-detail-title")).toBeVisible({ timeout: 15_000 });
}

test.describe("Runs-first refactor", () => {
  test("Runs is the first side-nav item on an agent detail page", async ({ page }) => {
    await openFirstAgent(page);
    await expect(page.getByTestId("runs-section")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("traces-tab")).toBeVisible(); // inner TracesTab testid preserved
    // Side-nav exposes a `nav-runs-section` testid (DetailSideNav renders `nav-${item.id}`).
    await expect(page.getByTestId("nav-runs-section")).toBeVisible();
  });

  test("Advanced group is collapsed by default and toggles open", async ({ page }) => {
    await openFirstAgent(page);
    const toggle = page.getByTestId("nav-group-toggle-advanced");
    await expect(toggle).toBeVisible();
    // Child nav items (deployments/endpoints/secrets/logs) not rendered while collapsed.
    await expect(page.getByTestId("nav-deployments-section")).toHaveCount(0);
    await toggle.click();
    await expect(page.getByTestId("nav-deployments-section")).toBeVisible();
    await expect(page.getByTestId("nav-endpoints-section")).toBeVisible();
    await expect(page.getByTestId("nav-secrets-section")).toBeVisible();
    await expect(page.getByTestId("nav-logs-section")).toBeVisible();
  });

  test("Advanced group open state persists across tab reload", async ({ page }) => {
    await openFirstAgent(page);
    await page.getByTestId("nav-group-toggle-advanced").click();
    await expect(page.getByTestId("nav-deployments-section")).toBeVisible();
    await page.reload();
    await expect(page.getByTestId("agent-detail-title")).toBeVisible({ timeout: 15_000 });
    // sessionStorage persists across reload in same tab.
    await expect(page.getByTestId("nav-deployments-section")).toBeVisible();
  });

  test("Clicking a run row updates URL to /agents/:id/runs/:sessionId", async ({ page }) => {
    await openFirstAgent(page);
    const firstRow = page.locator("[data-testid^='session-row-']").first();
    const hasRuns = await firstRow.count();
    test.skip(hasRuns === 0, "No runs to test row-click against in this environment");
    const sessionId = (await firstRow.getAttribute("data-testid"))!.replace(/^session-row-/, "");
    await firstRow.click();
    // The URL is pushed with encodeURIComponent(sessionId) so colons in ISO timestamps become %3A.
    const encoded = encodeURIComponent(sessionId);
    await expect(page).toHaveURL(new RegExp(`#/agents/[^/]+/runs/${encoded.replace(/[.*+?^${}()|[\\]\\\\]/g, "\\\\$&")}$`), { timeout: 5_000 });
  });

  test("Deep-linking /agents/:id/runs/:sessionId pre-selects the run", async ({ page }) => {
    await openFirstAgent(page);
    const firstRow = page.locator("[data-testid^='session-row-']").first();
    const hasRuns = await firstRow.count();
    test.skip(hasRuns === 0, "No runs to test deep-link against in this environment");
    const sessionId = (await firstRow.getAttribute("data-testid"))!.replace(/^session-row-/, "");
    const currentUrl = page.url();
    // Strip any trailing `/runs/...` if present, then append the deep-link path.
    const base = currentUrl.replace(/\/runs\/[^/?#]+$/, "");
    await page.goto(`${base}/runs/${encodeURIComponent(sessionId)}`);
    await expect(page.getByTestId("agent-detail-title")).toBeVisible({ timeout: 15_000 });
    // The "select a session" placeholder in TracesTab is hidden when a run is selected.
    await expect(page.getByText(/select a session/i)).toHaveCount(0);
  });

  test("Trigger-source badge renders for each run row (when runs exist)", async ({ page }) => {
    await openFirstAgent(page);
    const rows = page.locator("[data-testid^='session-row-']");
    const count = await rows.count();
    test.skip(count === 0, "No runs to assert badges against in this environment");
    for (let i = 0; i < Math.min(count, 5); i++) {
      const testId = await rows.nth(i).getAttribute("data-testid");
      const sessionId = testId!.replace(/^session-row-/, "");
      await expect(page.getByTestId(`run-source-${sessionId}`)).toBeVisible();
    }
  });
});
