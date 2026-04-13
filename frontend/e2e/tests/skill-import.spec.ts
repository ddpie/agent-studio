import { test, expect } from "../fixtures/test";
import { testName } from "../helpers/utils";
import path from "path";
import fs from "fs";

test.describe("Skill Import", () => {
  test("import skill from local .md file", async ({ page, skillsPage }) => {
    await skillsPage.goto();

    // Create a temporary .md file for import
    const tmpDir = path.join(process.cwd(), "e2e", "tmp");
    fs.mkdirSync(tmpDir, { recursive: true });
    const skillName = testName("import");
    const tmpFile = path.join(tmpDir, `${skillName}.md`);
    fs.writeFileSync(tmpFile, `---
name: "${skillName}"
description: "Imported by E2E test"
type: "prompt"
source: "manual"
user-invocable: true
---

# ${skillName}

This skill was imported by E2E test.
`);

    // Click "Import Local" / "本地文件" button
    const importBtn = page.getByRole("button", { name: /import|本地文件/i }).first();
    await importBtn.click();

    // The button triggers a hidden file input
    const fileInput = page.locator("input[type='file'][accept*='.md']");
    await fileInput.setInputFiles(tmpFile);

    // Wait for import to complete
    await page.waitForTimeout(3_000);

    // Refresh the list
    await skillsPage.goto();
    await page.waitForTimeout(1_000);

    // Search for the imported skill
    await skillsPage.searchSkill(skillName);
    await page.waitForTimeout(500);
    await expect(skillsPage.getSkillCard(skillName)).toBeVisible({ timeout: 5_000 });

    // Cleanup: delete the imported skill
    await skillsPage.openSkill(skillName);
    const deleteBtn = page.locator("button").filter({ has: page.locator("svg.lucide-trash-2") }).first();
    await deleteBtn.click();
    const confirmDialog = page.locator(".fixed.inset-0").last();
    await confirmDialog.waitFor({ state: "visible" });
    await confirmDialog.getByRole("button", { name: /trash|回收站/i }).click();

    // Cleanup temp file
    fs.unlinkSync(tmpFile);
    fs.rmdirSync(tmpDir, { recursive: true });
  });

  test("URL import dialog opens and validates input", async ({ page, skillsPage }) => {
    await skillsPage.goto();

    // Click "URL" import button
    const urlBtn = page.getByRole("button", { name: /url/i }).first();
    await urlBtn.click();

    // URL import dialog should appear
    const dialog = page.locator(".fixed.inset-0").last();
    await expect(dialog).toBeVisible({ timeout: 5_000 });

    // Should have URL input
    const urlInput = dialog.locator("input");
    await expect(urlInput).toBeVisible();

    // Import button should be disabled when empty
    const importConfirmBtn = dialog.getByRole("button", { name: /import|导入/i });
    await expect(importConfirmBtn).toBeDisabled();

    // Type a URL — import button should become enabled
    await urlInput.fill("https://example.com/skill.md");
    await expect(importConfirmBtn).toBeEnabled();

    // Cancel
    await dialog.getByRole("button", { name: /cancel|取消/i }).click();
    await page.waitForTimeout(300);
  });
});
