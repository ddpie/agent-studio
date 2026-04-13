import { test, expect } from "../fixtures/test";
import { testName } from "../helpers/utils";

const SKILL_NAME = testName("fileops");

test.describe("Skill File Operations", () => {
  let skillUrl: string;

  test.beforeAll(async ({ browser }) => {
    // Create a skill to work with
    const context = await browser.newContext({ storageState: ".auth/user.json" });
    const page = await context.newPage();
    await page.goto("/#/skills");
    await page.waitForLoadState("networkidle");

    // Create skill
    await page.getByRole("button", { name: /create|创建/i }).click();
    const dialog = page.locator(".fixed.inset-0").last();
    await dialog.waitFor({ state: "visible" });
    await dialog.locator("input").first().fill(SKILL_NAME);
    await dialog.getByRole("button", { name: /create|创建/i }).click();
    await page.waitForURL(/#\/skills\//, { timeout: 15_000 });
    skillUrl = page.url();
    await context.close();
  });

  test("create new file, edit, and save", async ({ page, skillDetailPage }) => {
    await page.goto(skillUrl.replace(/.*#/, "/#"));
    await expect(skillDetailPage.editorContainer).toBeVisible({ timeout: 15_000 });

    // Click new file button (Plus icon in the file tree area)
    const newFileBtn = page.locator("button[title*='file'], button[title*='文件']").filter({ has: page.locator("svg.lucide-file-plus, svg.lucide-plus") }).first();
    await newFileBtn.click();

    // Dialog should appear for filename
    const fileDialog = page.locator(".fixed.inset-0").last();
    await fileDialog.waitFor({ state: "visible", timeout: 5_000 });
    await fileDialog.locator("input").fill("test-script.py");
    await fileDialog.getByRole("button", { name: /create|创建|ok|confirm|确认/i }).click();

    // New file should appear in the tree and be selected
    await page.waitForTimeout(1_000);
    await expect(page.getByText("test-script.py").first()).toBeVisible();

    // Edit the new file content
    await skillDetailPage.setEditorContent('print("hello from e2e test")');

    // Save button should be visible
    await expect(skillDetailPage.saveButton).toBeVisible({ timeout: 5_000 });
    await skillDetailPage.save();

    // Reload and verify the file persisted
    await page.reload();
    await expect(skillDetailPage.editorContainer).toBeVisible({ timeout: 15_000 });
    await page.waitForTimeout(2_000);

    // The file should still be in the tree
    await expect(page.getByText("test-script.py").first()).toBeVisible();
  });

  test("delete file from tree", async ({ page, skillDetailPage }) => {
    await page.goto(skillUrl.replace(/.*#/, "/#"));
    await expect(skillDetailPage.editorContainer).toBeVisible({ timeout: 15_000 });
    await page.waitForTimeout(2_000);

    // Check if test-script.py exists from previous test
    const fileNode = page.getByText("test-script.py");
    const fileExists = await fileNode.isVisible().catch(() => false);
    if (!fileExists) {
      test.skip(true, "test-script.py not found — previous test may have failed");
      return;
    }

    // Right-click or find delete action for the file
    await fileNode.click({ button: "right" });
    await page.waitForTimeout(300);

    // Context menu or inline action — look for delete option
    const deleteOption = page.getByText(/delete|删除/i).last();
    if (await deleteOption.isVisible()) {
      await deleteOption.click();
    }

    // Confirm deletion if dialog appears
    const confirmDialog = page.locator(".fixed.inset-0").last();
    if (await confirmDialog.isVisible().catch(() => false)) {
      await confirmDialog.getByRole("button", { name: /delete|删除|confirm|确认/i }).click();
    }

    // Save the deletion
    if (await skillDetailPage.saveButton.isVisible().catch(() => false)) {
      await skillDetailPage.save();
    }
  });

  test("discard pending changes reverts all operations", async ({ page, skillDetailPage }) => {
    await page.goto(skillUrl.replace(/.*#/, "/#"));
    await expect(skillDetailPage.editorContainer).toBeVisible({ timeout: 15_000 });

    // Edit SKILL.md content
    await skillDetailPage.appendToEditor("\n\n## Discard Test\nThis should be reverted.");

    // Discard button should appear
    await expect(skillDetailPage.discardButton).toBeVisible({ timeout: 5_000 });

    // Click discard
    await skillDetailPage.discardButton.click();
    await page.waitForTimeout(500);

    // Save button should be gone (no pending changes)
    await expect(skillDetailPage.saveButton).toBeHidden({ timeout: 5_000 });

    // Content should not contain the discarded text
    const content = await skillDetailPage.getEditorContent();
    expect(content).not.toContain("Discard Test");
  });

  test.afterAll(async ({ browser }) => {
    // Cleanup: delete the test skill
    const context = await browser.newContext({ storageState: ".auth/user.json" });
    const page = await context.newPage();
    await page.goto(skillUrl.replace(/.*#/, "/#"));
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2_000);

    const deleteBtn = page.locator("button").filter({ has: page.locator("svg.lucide-trash-2") }).first();
    if (await deleteBtn.isVisible()) {
      await deleteBtn.click();
      const confirmDialog = page.locator(".fixed.inset-0").last();
      await confirmDialog.waitFor({ state: "visible" });
      await confirmDialog.getByRole("button", { name: /trash|回收站/i }).click();
    }
    await context.close();
  });
});
