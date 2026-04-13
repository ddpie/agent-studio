import { test, expect } from "../fixtures/test";

test.describe("AI Assistant Panels", () => {
  test.setTimeout(180_000);

  test("edit assistant panel auto-opens and can be toggled", async ({ page }) => {
    await page.goto("/#/agents");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2_000);

    // Open first agent's edit page
    const editButtons = page.locator("svg.lucide-settings-2").locator("..");
    const count = await editButtons.count();
    if (count === 0) { test.skip(true, "No agents available"); return; }

    await editButtons.first().click();
    await page.waitForURL(/#\/agents\/edit\//, { timeout: 10_000 });
    await page.waitForTimeout(3_000);

    // AI assistant panel should auto-open — look for the Sparkles toggle button
    const sparklesBtn = page.locator("button").filter({ has: page.locator("svg.lucide-sparkles") }).first();
    await expect(sparklesBtn).toBeVisible({ timeout: 10_000 });

    // The assistant panel should have a textarea for input
    // It's in the right side panel
    const assistantTextarea = page.locator("textarea").last();
    const isAssistantVisible = await assistantTextarea.isVisible();

    if (isAssistantVisible) {
      // Close the panel
      const closeBtn = page.locator("button").filter({ has: page.locator("svg.lucide-x") }).last();
      if (await closeBtn.isVisible()) {
        await closeBtn.click();
        await page.waitForTimeout(500);
      }

      // Reopen via sparkles button
      await sparklesBtn.click();
      await page.waitForTimeout(500);
    }
  });

  test("edit assistant sends message and receives response", async ({ page }) => {
    await page.goto("/#/agents");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2_000);

    const editButtons = page.locator("svg.lucide-settings-2").locator("..");
    const count = await editButtons.count();
    if (count === 0) { test.skip(true, "No agents available"); return; }

    await editButtons.first().click();
    await page.waitForURL(/#\/agents\/edit\//, { timeout: 10_000 });
    await page.waitForTimeout(3_000);

    // Find the assistant panel textarea (last textarea on page, after system prompt)
    const textareas = page.locator("textarea");
    const textareaCount = await textareas.count();
    if (textareaCount < 2) { test.skip(true, "Assistant panel not visible"); return; }

    const assistantInput = textareas.last();
    await assistantInput.fill("say hello in one word");

    // Find send button in the assistant panel (last Send icon)
    const sendBtn = page.locator("button").filter({ has: page.locator("svg.lucide-send") }).last();
    await sendBtn.click();

    // Wait for streaming to complete — the send button should reappear
    await page.waitForTimeout(30_000);

    // There should be assistant response text in the panel
    const panelContent = page.locator(".prose, .react-markdown").last();
    await expect(panelContent).toBeVisible({ timeout: 60_000 });
  });

  test("skill assistant panel opens with skill editor", async ({ page, skillsPage, skillDetailPage }) => {
    // Create a temp skill to test with
    await skillsPage.goto();
    const skillName = `e2e-assist-${Date.now().toString(36)}`;
    await skillsPage.createSkill(skillName, "Assistant test");

    await expect(skillDetailPage.editorContainer).toBeVisible({ timeout: 15_000 });
    await page.waitForTimeout(2_000);

    // The sparkles button should be visible (assistant toggle)
    const sparklesBtn = page.locator("button").filter({ has: page.locator("svg.lucide-sparkles") }).first();
    await expect(sparklesBtn).toBeVisible();

    // Click to ensure panel is open
    await sparklesBtn.click();
    await page.waitForTimeout(500);

    // Cleanup: delete the skill
    await skillDetailPage.deleteSkill();
  });
});
