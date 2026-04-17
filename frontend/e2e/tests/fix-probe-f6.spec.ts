import { test, expect } from "../fixtures/test";

/**
 * F6 probe — verifies list_agents returns archived agents.
 *
 * Before the fix, CRUD list_agents applied a FilterExpression that excluded
 * archived agents, so the frontend's `archivedAgents` state was always
 * empty and the "Archived" section toggle never rendered. That broke
 * restore/purge flows entirely.
 *
 * This probe verifies the frontend receives archived agents:
 *   1. The `/agents` API response must include at least one archived item
 *      (assuming test account has ≥1 archived — which F1 probe leaves behind).
 *   2. The "已归档 / Archived" section toggle must be visible in the UI.
 */

test.describe("F6 probe: list_agents returns archived agents", () => {
  test.setTimeout(60_000);

  test("archived agents are returned in API and rendered in sidebar toggle", async ({ page }) => {
    // Intercept the agents list response to verify payload contains archived.
    let hasArchived = false;
    let sawResponse = false;
    page.on("response", async (resp) => {
      const url = resp.url();
      if (!url.includes("/agents") || resp.request().method() !== "GET") return;
      if (!/workspaces\/[^/]+\/agents(\?|$)/.test(url)) return;
      try {
        const body = await resp.json();
        const items = body.items || [];
        sawResponse = true;
        if (items.some((it: { status?: string }) => it.status === "archived")) {
          hasArchived = true;
        }
      } catch { /* ignore */ }
    });

    await page.goto("/#/agents");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2_000);

    expect(sawResponse, "No /agents API response captured").toBe(true);
    expect(
      hasArchived,
      "F6 BROKEN: /agents API did not return any archived agents. " +
      "Precondition: test account must have >=1 archived agent (F1 probe leaves one).",
    ).toBe(true);

    // UI assertion: archived toggle should be visible.
    const archivedToggle = page.getByText(/archived|已归档/i).first();
    await expect(
      archivedToggle,
      "F6 BROKEN: archived section toggle not visible in sidebar despite archived agents in API",
    ).toBeVisible({ timeout: 10_000 });
  });
});
