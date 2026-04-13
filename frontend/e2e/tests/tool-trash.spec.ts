import { test, expect } from "../fixtures/test";

test.describe("Tool Trash", () => {
  test("trash view opens and returns to active list", async ({ page, toolsPage }) => {
    await toolsPage.goto();

    // The trash button has title "回收站" (from t("skills.trash"))
    const trashBtn = page.locator('button[title="回收站"], button[title="Trash"]');
    await trashBtn.click();
    await page.waitForTimeout(1_000);

    // Heading should change to "回收站" / "Trash"
    const heading = page.locator("h2").first();
    await expect(heading).toContainText(/trash|回收站/i, { timeout: 5_000 });

    // Back button should return to active list
    const backBtn = page.locator("button").filter({ has: page.locator("svg.lucide-chevron-left") }).first();
    await backBtn.click();
    await page.waitForTimeout(500);

    // Heading should be back to normal (Tools / 工具)
    await expect(heading).not.toContainText(/trash|回收站/i);
  });

  test("create tool, delete, verify gone from active list", async ({ page, toolsPage, toolDetailPage }) => {
    await toolsPage.goto();
    const toolName = `e2e-trash-${Date.now().toString(36)}`;
    await toolsPage.createTool(toolName, "Trash test tool");
    await expect(toolDetailPage.editorContainer).toBeVisible({ timeout: 10_000 });

    // Save the template
    await toolDetailPage.save();
    // Wait for redirect after save
    await page.waitForTimeout(3_000);

    // Delete it
    await toolDetailPage.deleteTool();

    // Go back to tools list and verify it's gone
    await toolsPage.goto();
    await toolsPage.searchTool(toolName);
    await page.waitForTimeout(500);
    await expect(toolsPage.getToolCard(toolName)).toBeHidden();
  });
});
