import { test, expect } from "../fixtures/test";

test.describe("Agent CRUD", () => {
  test("agent list loads and chat panel works", async ({ page }) => {
    await page.goto("/#/agents");
    await page.waitForLoadState("networkidle");

    // ── 1. Agent sidebar should be visible with Meta-Agent button ──
    const metaAgentButton = page.getByRole("button", { name: /Meta Agent/i }).first();
    await expect(metaAgentButton).toBeVisible({ timeout: 15_000 });

    // ── 2. Click Meta-Agent → chat heading should appear ──
    await metaAgentButton.click();
    await page.waitForTimeout(1_000);

    // Chat area should have an input (textarea or input for message)
    const chatArea = page.locator("textarea").first();
    await expect(chatArea).toBeVisible({ timeout: 10_000 });
  });

  test("edit agent form loads correctly", async ({ page }) => {
    await page.goto("/#/agents");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2_000);

    // Find edit buttons (Settings2 icon)
    const editButtons = page.locator("svg.lucide-settings-2").locator("..");
    const editCount = await editButtons.count();

    if (editCount === 0) {
      test.skip(true, "No agents available to test editing");
      return;
    }

    // Click the first edit button
    await editButtons.first().click();
    await page.waitForURL(/#\/agents\/edit\//, { timeout: 10_000 });

    // ── 1. Form should load — wait for the form sections to render ──
    // The "Basic Info" section heading should be visible
    const basicInfo = page.getByText(/basic info|基本信息/i).first();
    await expect(basicInfo).toBeVisible({ timeout: 15_000 });

    // ── 2. Agent name field should be populated (disabled for existing agents) ──
    const nameInput = page.locator("input").first();
    await expect(nameInput).toBeVisible();
    const nameValue = await nameInput.inputValue();
    expect(nameValue.length).toBeGreaterThan(0);

    // ── 3. System prompt section should exist ──
    const promptSection = page.getByText(/system prompt|系统提示/i).first();
    await expect(promptSection).toBeVisible();
  });
});
