import { test, expect } from "../fixtures/test";

test.describe("Agent Edit — Deep Interactions", () => {
  /** Helper: navigate to the first available agent's edit page */
  async function openFirstAgentEdit(page: import("@playwright/test").Page) {
    await page.goto("/#/agents");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2_000);

    const editButtons = page.locator("svg.lucide-settings-2").locator("..");
    const count = await editButtons.count();
    if (count === 0) return false;

    await editButtons.first().click();
    await page.waitForURL(/#\/agents\/edit\//, { timeout: 10_000 });

    // Wait for form to load
    await page.getByText(/basic info|基本信息/i).first().waitFor({ state: "visible", timeout: 15_000 });
    return true;
  }

  test("system prompt section is editable", async ({ page }) => {
    const opened = await openFirstAgentEdit(page);
    if (!opened) { test.skip(true, "No agents available"); return; }

    // Find system prompt section
    const promptSection = page.getByText(/system prompt|系统提示/i).first();
    await expect(promptSection).toBeVisible();

    // The system prompt is a textarea
    const promptTextarea = page.locator("textarea").first();
    await expect(promptTextarea).toBeVisible();

    // Verify it has content (existing agent should have a prompt)
    const value = await promptTextarea.inputValue();
    expect(value.length).toBeGreaterThan(0);
  });

  test("skills section shows and opens picker", async ({ page }) => {
    const opened = await openFirstAgentEdit(page);
    if (!opened) { test.skip(true, "No agents available"); return; }

    // The add button text is "添加技能" / "Add Skill" with a Plus icon
    const addSkillBtn = page.locator("button").filter({ has: page.locator("svg.lucide-plus") }).filter({ hasText: /添加技能|Add/i }).first();
    await addSkillBtn.scrollIntoViewIfNeeded();
    await addSkillBtn.click();

    // SkillPicker modal should appear with a search input
    const pickerModal = page.locator(".fixed.inset-0").last();
    await expect(pickerModal).toBeVisible({ timeout: 5_000 });

    const searchInput = pickerModal.locator("input");
    await expect(searchInput).toBeVisible();

    // Verify the modal loaded — just check the search input is there
    await page.waitForTimeout(2_000);

    // Close the picker via X button
    const closeBtn = pickerModal.locator("button").filter({ has: page.locator("svg.lucide-x") });
    await closeBtn.click();
    await page.waitForTimeout(500);
  });

  test("secrets section — add row, fill, remove", async ({ page }) => {
    const opened = await openFirstAgentEdit(page);
    if (!opened) { test.skip(true, "No agents available"); return; }

    // Scroll to "添加密钥" / "Add Secret" button
    const addSecretBtn = page.getByText(/add secret|添加密钥/i);
    await addSecretBtn.scrollIntoViewIfNeeded();
    await addSecretBtn.click();

    // A row should appear with key (font-mono) + value (password) inputs
    const keyInput = page.locator("input.font-mono").first();
    await expect(keyInput).toBeVisible({ timeout: 5_000 });
    await keyInput.fill("E2E_TEST_KEY");

    const valueInput = page.locator("input[type='password']").first();
    await expect(valueInput).toBeVisible();
    await valueInput.fill("test-secret-value");

    // Toggle show/hide — button has Eye icon + "显示"/"Show" text
    const showBtn = page.locator("button").filter({ has: page.locator("svg.lucide-eye") }).first();
    await showBtn.click();
    await page.waitForTimeout(300);

    // Remove the row via trash icon
    // Find the trash button that's a sibling of the key/value inputs
    const trashButtons = page.locator("button").filter({ has: page.locator("svg.lucide-trash-2") });
    // The last trash button should be the one for the secret row
    await trashButtons.last().click();

    // Key input should be gone
    await expect(keyInput).toBeHidden({ timeout: 3_000 });
  });

  test("model selector in form works", async ({ page }) => {
    const opened = await openFirstAgentEdit(page);
    if (!opened) { test.skip(true, "No agents available"); return; }

    // Find model selector (select element or custom dropdown)
    const modelSection = page.getByText(/model|模型/i).first();
    await expect(modelSection).toBeVisible();
  });

  test("tools section — open ToolPicker and close", async ({ page }) => {
    const opened = await openFirstAgentEdit(page);
    if (!opened) { test.skip(true, "No agents available"); return; }

    // Find the "添加工具" / "Add Tool" button specifically
    const addToolBtn = page.locator("button").filter({ hasText: /添加工具|Add Tool/i }).first();
    await addToolBtn.scrollIntoViewIfNeeded();
    await addToolBtn.click();

    // ToolPicker modal should appear
    const pickerModal = page.locator(".fixed.inset-0").last();
    await expect(pickerModal).toBeVisible({ timeout: 5_000 });

    // Should have search input
    const searchInput = pickerModal.locator("input");
    await expect(searchInput).toBeVisible();

    // Search for a tool
    await searchInput.fill("search");
    await page.waitForTimeout(500);

    // Close via X
    const closeBtn = pickerModal.locator("button").filter({ has: page.locator("svg.lucide-x") });
    await closeBtn.click();
    await page.waitForTimeout(500);
  });

  test("tools section — collapse and expand tool block", async ({ page }) => {
    const opened = await openFirstAgentEdit(page);
    if (!opened) { test.skip(true, "No agents available"); return; }

    // Look for tool blocks — they have @tool label and function name
    const toolHeaders = page.locator("button, div").filter({ hasText: /@tool/i });
    const toolCount = await toolHeaders.count();

    if (toolCount === 0) {
      test.skip(true, "No tools in this agent to test collapse");
      return;
    }

    // Click the first tool header to toggle collapse
    const firstToolHeader = toolHeaders.first();
    await firstToolHeader.scrollIntoViewIfNeeded();
    await firstToolHeader.click();
    await page.waitForTimeout(300);

    // Click again to toggle back
    await firstToolHeader.click();
    await page.waitForTimeout(300);
  });
});
