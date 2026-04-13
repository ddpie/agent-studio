import { test, expect } from "../fixtures/test";
import { testName } from "../helpers/utils";

const SKILL_NAME = testName("skill");
const SKILL_DESC = "E2E test skill — safe to delete";

test.describe("Skill Management", () => {
  test("create → edit → save → validate → delete", async ({ page, skillsPage, skillDetailPage }) => {
    // ── 1. Navigate to Skills page ──
    await skillsPage.goto();
    await expect(skillsPage.heading.first()).toBeVisible();

    // ── 2. Create a new skill ──
    await skillsPage.createSkill(SKILL_NAME, SKILL_DESC);

    // Should land on skill detail page with SKILL.md loaded
    await expect(skillDetailPage.skillName).toContainText(SKILL_NAME, { timeout: 10_000 });

    // Monaco editor should be visible with the generated SKILL.md content
    await expect(skillDetailPage.editorContainer).toBeVisible({ timeout: 10_000 });

    // ── 3. Edit the SKILL.md content ──
    await skillDetailPage.appendToEditor("\n## Instructions\nThis is an e2e test skill.");

    // Save button should appear (dirty state)
    await expect(skillDetailPage.saveButton).toBeVisible({ timeout: 5_000 });

    // ── 4. Save changes ──
    await skillDetailPage.save();

    // ── 5. Verify persistence — reload and check content survived ──
    await page.reload();
    await expect(skillDetailPage.editorContainer).toBeVisible({ timeout: 15_000 });
    await page.waitForTimeout(2_000);
    const editorText = await skillDetailPage.getEditorContent();
    expect(editorText).toContain("e2e test skill");

    // ── 6. Validate the skill ──
    await skillDetailPage.validate();

    // ── 7. Delete the skill (cleanup) ──
    await skillDetailPage.deleteSkill();

    // Should be back on skills list
    await expect(page).toHaveURL(/#\/skills/);

    // The deleted skill should not appear in the active list
    await skillsPage.searchSkill(SKILL_NAME);
    await page.waitForTimeout(500);
    await expect(skillsPage.getSkillCard(SKILL_NAME)).toBeHidden();
  });
});
