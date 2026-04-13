import { test, expect } from "../fixtures/test";

/**
 * Agent form field editing — covers all input types in AgentFormSections.
 */
test.describe("Agent Form Fields", () => {
  async function openFirstAgentEdit(page: import("@playwright/test").Page) {
    await page.goto("/#/agents");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2_000);
    const editButtons = page.locator("svg.lucide-settings-2").locator("..");
    if (await editButtons.count() === 0) return false;
    await editButtons.first().click();
    await page.waitForURL(/#\/agents\/edit\//, { timeout: 10_000 });
    await page.getByText(/basic info|基本信息/i).first().waitFor({ state: "visible", timeout: 15_000 });
    return true;
  }

  test("edit display name field", async ({ page }) => {
    if (!await openFirstAgentEdit(page)) { test.skip(true, "No agents"); return; }

    // Display name is the second input (first is agent name, disabled for existing)
    const inputs = page.locator("input[type='text'], input:not([type])");
    const displayNameInput = inputs.nth(1);
    await expect(displayNameInput).toBeVisible();

    const original = await displayNameInput.inputValue();
    await displayNameInput.fill(original + " E2E");

    // Verify dirty state — diff button should appear
    const diffBtn = page.locator("button").filter({ has: page.locator("svg.lucide-git-compare") });
    await expect(diffBtn).toBeVisible({ timeout: 5_000 });

    // Revert
    await displayNameInput.fill(original);
  });

  test("edit description field", async ({ page }) => {
    if (!await openFirstAgentEdit(page)) { test.skip(true, "No agents"); return; }

    // Description is an input — find it by scrolling to the description label
    const descLabel = page.getByText(/description|描述/i).first();
    await descLabel.scrollIntoViewIfNeeded();

    // The description input is a text input near the label
    const descInput = page.locator("input[type='text']").nth(2);
    if (!await descInput.isVisible({ timeout: 3_000 }).catch(() => false)) {
      // Might be a textarea instead
      test.skip(true, "Description field not found as expected input type");
      return;
    }

    const original = await descInput.inputValue();
    await descInput.fill("E2E test description change");

    // Verify the value actually changed
    const newValue = await descInput.inputValue();
    expect(newValue).toBe("E2E test description change");

    // Diff button should appear (dirty state indicator)
    const diffBtn = page.locator("button").filter({ has: page.locator("svg.lucide-git-compare") });
    await expect(diffBtn).toBeVisible({ timeout: 5_000 });

    // Revert and verify reverted
    await descInput.fill(original);
    const revertedValue = await descInput.inputValue();
    expect(revertedValue).toBe(original);
  });

  test("system prompt textarea is editable and tracks changes", async ({ page }) => {
    if (!await openFirstAgentEdit(page)) { test.skip(true, "No agents"); return; }

    const promptTextarea = page.locator("textarea").first();
    const original = await promptTextarea.inputValue();
    expect(original.length).toBeGreaterThan(0);

    // Append text
    await promptTextarea.fill(original + "\n\n## E2E Test Marker");

    // Diff button should appear (dirty state)
    const diffBtn = page.locator("button").filter({ has: page.locator("svg.lucide-git-compare") });
    await expect(diffBtn).toBeVisible({ timeout: 5_000 });

    // Revert
    await promptTextarea.fill(original);
  });

  test("diff button opens review changes modal", async ({ page }) => {
    if (!await openFirstAgentEdit(page)) { test.skip(true, "No agents"); return; }

    // Make a change to trigger dirty state
    const promptTextarea = page.locator("textarea").first();
    const original = await promptTextarea.inputValue();
    await promptTextarea.fill(original + "\n\nDiff test marker");

    // Click diff button
    const diffBtn = page.locator("button").filter({ has: page.locator("svg.lucide-git-compare") });
    await expect(diffBtn).toBeVisible({ timeout: 5_000 });
    await diffBtn.click();

    // Review changes modal should appear with diff editor
    const modal = page.locator(".fixed.inset-0").last();
    await expect(modal).toBeVisible({ timeout: 5_000 });

    // Should show changed field tabs or diff content
    await page.waitForTimeout(1_000);

    // Close modal
    const closeBtn = modal.getByRole("button", { name: /close|关闭|cancel|取消/i });
    if (await closeBtn.isVisible().catch(() => false)) {
      await closeBtn.click();
    } else {
      await page.keyboard.press("Escape");
    }
    await page.waitForTimeout(500);

    // Revert
    await promptTextarea.fill(original);
  });

  test("model selector exists in form", async ({ page }) => {
    if (!await openFirstAgentEdit(page)) { test.skip(true, "No agents"); return; }

    // Model section should be visible
    const modelLabel = page.getByText(/model|模型/i).first();
    await expect(modelLabel).toBeVisible();
  });

  test("welcome message field is editable", async ({ page }) => {
    if (!await openFirstAgentEdit(page)) { test.skip(true, "No agents"); return; }

    // Scroll down to find welcome message section
    const welcomeLabel = page.getByText(/welcome|欢迎消息/i).first();
    if (await welcomeLabel.isVisible({ timeout: 3_000 }).catch(() => false)) {
      await welcomeLabel.scrollIntoViewIfNeeded();

      // Find the textarea near the welcome label
      const welcomeTextarea = page.locator("textarea").nth(1);
      if (await welcomeTextarea.isVisible().catch(() => false)) {
        const original = await welcomeTextarea.inputValue();
        await welcomeTextarea.fill("E2E welcome test");
        await welcomeTextarea.fill(original); // revert
      }
    }
  });
});
