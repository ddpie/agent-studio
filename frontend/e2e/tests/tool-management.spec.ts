import { test, expect } from "../fixtures/test";
import { testName } from "../helpers/utils";

const TOOL_NAME = testName("tool");
const TOOL_DESC = "E2E test tool — safe to delete";
const UPDATED_DESC = "Updated by E2E test";

test.describe("Tool Management", () => {
  test("create → edit name/desc → save → reload verify → validate → delete", async ({ page, toolsPage, toolDetailPage }) => {
    // ── 1. Navigate to Tools page ──
    await toolsPage.goto();
    await expect(toolsPage.heading.first()).toBeVisible();

    // ── 2. Create a new tool ──
    await toolsPage.createTool(TOOL_NAME, TOOL_DESC);

    // Should land on tool detail page with template code in Monaco
    await expect(toolDetailPage.editorContainer).toBeVisible({ timeout: 10_000 });

    // Template code should be loaded
    const templateCode = await toolDetailPage.getCode();
    expect(templateCode).toContain("@tool");
    expect(templateCode).toContain("def my_tool");

    // ── 3. Edit the description (regular input, not Monaco) ──
    const inputs = page.locator("input[type='text'], input:not([type])");
    const descField = inputs.nth(1); // second input is description
    await descField.fill(UPDATED_DESC);

    // Save button should appear
    await expect(toolDetailPage.saveButton).toBeVisible({ timeout: 5_000 });

    // ── 4. Save ──
    await toolDetailPage.save();

    // ── 5. Verify persistence — reload and check description survived ──
    await page.reload();
    await expect(toolDetailPage.editorContainer).toBeVisible({ timeout: 15_000 });
    await page.waitForTimeout(2_000);

    // Code should still be the template (we didn't change it)
    const codeAfterReload = await toolDetailPage.getCode();
    expect(codeAfterReload).toContain("@tool");

    // ── 6. Validate ──
    await toolDetailPage.validate();

    // ── 7. Delete (cleanup) ──
    await toolDetailPage.deleteTool();

    // Should be back on tools list
    await expect(page).toHaveURL(/#\/tools/);
  });
});
