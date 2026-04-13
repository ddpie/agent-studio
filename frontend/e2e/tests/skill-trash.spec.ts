import { test, expect } from "../fixtures/test";
import { testName } from "../helpers/utils";

const SKILL_NAME = testName("trash");

test.describe("Skill Trash", () => {
  test("delete skill → view in trash → restore", async ({ page, skillsPage }) => {
    // ── 1. Create a skill ──
    await skillsPage.goto();
    await skillsPage.createSkill(SKILL_NAME, "Trash test skill");

    // ── 2. Delete it ──
    const deleteBtn = page.locator("button").filter({ has: page.locator("svg.lucide-trash-2") }).first();
    await deleteBtn.click();
    const confirmDialog = page.locator(".fixed.inset-0").last();
    await confirmDialog.waitFor({ state: "visible" });
    await confirmDialog.getByRole("button", { name: /trash|回收站/i }).click();
    await page.waitForURL(/#\/skills/, { timeout: 10_000 });

    // ── 3. Open trash ──
    const trashBtn = page.locator("button").filter({ has: page.locator("svg.lucide-trash-2") }).first();
    await trashBtn.click();
    await page.waitForTimeout(1_000);

    // Should see the deleted skill
    await skillsPage.searchSkill(SKILL_NAME);
    await page.waitForTimeout(500);
    const trashedCard = skillsPage.getSkillCard(SKILL_NAME);
    await expect(trashedCard).toBeVisible({ timeout: 5_000 });

    // ── 4. Restore it ──
    const restoreBtn = trashedCard.locator("button").filter({ has: page.locator("svg.lucide-rotate-ccw") });
    await restoreBtn.click();
    await page.waitForTimeout(2_000);

    // ── 5. Go back to active list and verify it's restored ──
    const backBtn = page.locator("button").filter({ has: page.locator("svg.lucide-chevron-left") }).first();
    await backBtn.click();
    await page.waitForTimeout(1_000);

    await skillsPage.searchSkill(SKILL_NAME);
    await page.waitForTimeout(500);
    await expect(skillsPage.getSkillCard(SKILL_NAME)).toBeVisible({ timeout: 5_000 });

    // Cleanup: delete again permanently
    await skillsPage.openSkill(SKILL_NAME);
    const delBtn2 = page.locator("button").filter({ has: page.locator("svg.lucide-trash-2") }).first();
    await delBtn2.click();
    const confirm2 = page.locator(".fixed.inset-0").last();
    await confirm2.waitFor({ state: "visible" });
    await confirm2.getByRole("button", { name: /trash|回收站/i }).click();
  });
});
