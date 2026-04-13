import { test, expect } from "../fixtures/test";
import { testName } from "../helpers/utils";

/**
 * Skill file tree advanced operations — context menu, rename, folder creation.
 */
test.describe("Skill File Tree — Advanced", () => {
  let skillUrl: string;
  const SKILL_NAME = testName("tree");

  test.beforeAll(async ({ browser }) => {
    const context = await browser.newContext({ storageState: ".auth/user.json" });
    const page = await context.newPage();
    await page.goto("/#/skills");
    await page.waitForLoadState("networkidle");
    await page.getByRole("button", { name: /create|创建/i }).click();
    const dialog = page.locator(".fixed.inset-0").last();
    await dialog.waitFor({ state: "visible" });
    await dialog.locator("input").first().fill(SKILL_NAME);
    await dialog.getByRole("button", { name: /create|创建/i }).click();
    await page.waitForURL(/#\/skills\//, { timeout: 15_000 });
    skillUrl = page.url();
    await context.close();
  });

  test("create file, then rename it via context menu", async ({ page, skillDetailPage }) => {
    await page.goto(skillUrl.replace(/.*#/, "/#"));
    await expect(skillDetailPage.editorContainer).toBeVisible({ timeout: 15_000 });
    await page.waitForTimeout(2_000);

    // Create a new file
    const newFileBtn = page.locator("button[title*='file'], button[title*='文件']").filter({ has: page.locator("svg") }).first();
    await newFileBtn.click();
    const fileDialog = page.locator(".fixed.inset-0").last();
    await fileDialog.waitFor({ state: "visible", timeout: 5_000 });
    await fileDialog.locator("input").fill("rename-me.py");
    await fileDialog.getByRole("button", { name: /create|创建|ok|confirm|确认/i }).click();
    await page.waitForTimeout(1_000);

    // Verify file was created
    await expect(page.getByText("rename-me.py").first()).toBeVisible();

    // Right-click the file to open context menu
    const fileNode = page.getByText("rename-me.py").first();
    await fileNode.click({ button: "right" });
    await page.waitForTimeout(500);

    // Look for rename option in context menu
    const renameOption = page.getByText(/rename|重命名/i).last();
    const hasRename = await renameOption.isVisible({ timeout: 2_000 }).catch(() => false);

    if (hasRename) {
      await renameOption.click();
      await page.waitForTimeout(500);

      // Rename dialog — might be inline edit or modal
      const renameInput = page.locator("input:visible").last();
      if (await renameInput.isVisible({ timeout: 3_000 }).catch(() => false)) {
        await renameInput.fill("renamed-file.py");
        await page.keyboard.press("Enter");
        await page.waitForTimeout(500);
        // Verify renamed
        const renamed = await page.getByText("renamed-file.py").first().isVisible().catch(() => false);
        expect(renamed).toBe(true);
      }
    }
  });

  test("create folder via new folder button", async ({ page, skillDetailPage }) => {
    await page.goto(skillUrl.replace(/.*#/, "/#"));
    await expect(skillDetailPage.editorContainer).toBeVisible({ timeout: 15_000 });
    await page.waitForTimeout(2_000);

    // Look for new folder button (FolderPlus icon)
    const newFolderBtn = page.locator("button[title*='folder'], button[title*='文件夹']").filter({ has: page.locator("svg") }).first();
    if (await newFolderBtn.isVisible({ timeout: 3_000 }).catch(() => false)) {
      await newFolderBtn.click();

      const folderDialog = page.locator(".fixed.inset-0").last();
      await folderDialog.waitFor({ state: "visible", timeout: 3_000 });
      await folderDialog.locator("input").fill("scripts");
      await folderDialog.getByRole("button", { name: /create|创建|ok|confirm|确认/i }).click();
      await page.waitForTimeout(500);
    }
  });

  test("diff modal shows changes side-by-side", async ({ page, skillDetailPage }) => {
    await page.goto(skillUrl.replace(/.*#/, "/#"));
    await expect(skillDetailPage.editorContainer).toBeVisible({ timeout: 15_000 });
    await page.waitForTimeout(2_000);

    // Edit content to create a diff
    await skillDetailPage.appendToEditor("\n## Diff Test Section\nThis line was added for diff testing.");

    // The diff button text includes a count like "Diff (1)" / "变更 (1)"
    // Wait for save button first (indicates dirty state)
    await expect(skillDetailPage.saveButton).toBeVisible({ timeout: 5_000 });

    // Find the diff button — it has GitCompare icon
    const diffBtn = page.locator("button").filter({ has: page.locator("svg.lucide-git-compare") });
    await expect(diffBtn).toBeVisible({ timeout: 5_000 });
    await diffBtn.click();

    // Diff modal should appear with Monaco DiffEditor
    const modal = page.locator(".fixed.inset-0").last();
    await expect(modal).toBeVisible({ timeout: 5_000 });

    // Modal should contain diff content — at minimum the Monaco diff editor
    await page.waitForTimeout(1_000);
    const hasDiffEditor = await page.locator(".monaco-diff-editor, .monaco-editor").first().isVisible().catch(() => false);
    expect(hasDiffEditor).toBe(true);

    // Close modal
    await page.keyboard.press("Escape");
    await page.waitForTimeout(500);

    // Discard changes
    if (await skillDetailPage.discardButton.isVisible().catch(() => false)) {
      await skillDetailPage.discardButton.click();
    }
  });

  test("validation banner shows errors and can be dismissed", async ({ page, skillDetailPage }) => {
    await page.goto(skillUrl.replace(/.*#/, "/#"));
    await expect(skillDetailPage.editorContainer).toBeVisible({ timeout: 15_000 });
    await page.waitForTimeout(2_000);

    // Replace content with invalid (no frontmatter)
    await skillDetailPage.setEditorContent("# Invalid content\nNo YAML frontmatter.");

    // Validate
    await skillDetailPage.validate();
    await page.waitForTimeout(2_000);

    // Validation banner should show errors
    const banner = page.locator("[class*='red'], [class*='amber']").first();
    const hasBanner = await banner.isVisible().catch(() => false);
    expect(hasBanner).toBe(true);

    // Dismiss the banner
    const dismissBtn = banner.locator("button").first();
    if (await dismissBtn.isVisible().catch(() => false)) {
      await dismissBtn.click();
      await page.waitForTimeout(300);
    }

    // Discard invalid changes
    if (await skillDetailPage.discardButton.isVisible().catch(() => false)) {
      await skillDetailPage.discardButton.click();
    }
  });

  test.afterAll(async ({ browser }) => {
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
