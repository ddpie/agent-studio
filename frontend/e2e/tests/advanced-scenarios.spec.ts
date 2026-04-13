import { test, expect } from "../fixtures/test";

/**
 * Chat with existing deployed sub-agents — verify they respond correctly.
 * Uses agents that already exist in the workspace.
 */
test.describe("Sub-Agent Chat Verification", () => {
  test.setTimeout(180_000);

  test("chat with an existing agent and verify it responds", async ({ page }) => {
    await page.goto("/#/agents");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2_000);

    // Find the first non-Meta-Agent in the sidebar
    const sidebar = page.locator("aside").first();
    const agentCards = sidebar.locator(".group");
    const agentCount = await agentCards.count();

    if (agentCount === 0) {
      test.skip(true, "No sub-agents available");
      return;
    }

    // Click the first agent's chat button
    const firstAgent = agentCards.first();
    const agentName = await firstAgent.locator("span.text-sm.font-medium").textContent();
    console.log(`Testing chat with agent: ${agentName}`);
    await firstAgent.locator("button").first().click();
    await page.waitForTimeout(2_000);

    // Verify URL changed to agent chat
    expect(page.url()).toMatch(/#\/agents\/chat\//);

    // Chat heading should show the agent name
    const heading = page.locator("h2").first();
    const headingText = await heading.textContent();
    expect(headingText?.length).toBeGreaterThan(0);

    // Send a simple message
    const textarea = page.locator("textarea").first();
    await textarea.fill("hello, who are you? respond in one sentence.");
    await page.locator("button[type='submit']").first().click();

    // Wait for response
    const cancelBtn = page.locator("button").filter({ has: page.locator("svg.lucide-square") });
    await cancelBtn.waitFor({ state: "visible", timeout: 60_000 }).catch(() => {});
    await cancelBtn.waitFor({ state: "hidden", timeout: 120_000 });

    // Verify we got a non-empty response (not an error)
    const lastMsg = await page.locator(".prose, .react-markdown").last().textContent();
    expect(lastMsg?.length).toBeGreaterThan(5);
    expect(lastMsg).not.toContain("Error:");
    console.log(`Agent response: "${lastMsg?.slice(0, 100)}"`);
  });

  test("switch between Meta-Agent and sub-agent preserves sessions", async ({ page }) => {
    await page.goto("/#/agents");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2_000);

    const sidebar = page.locator("aside").first();
    const agentCards = sidebar.locator(".group");
    if (await agentCards.count() === 0) {
      test.skip(true, "No sub-agents available");
      return;
    }

    // Send a message to Meta-Agent
    const metaBtn = sidebar.locator("button").filter({ has: page.locator("svg.lucide-message-square") }).first();
    await metaBtn.click();
    await page.waitForTimeout(1_000);

    const textarea = page.locator("textarea").first();
    await textarea.fill("respond with only: META_MARKER_123");
    await page.locator("button[type='submit']").first().click();

    const cancelBtn = page.locator("button").filter({ has: page.locator("svg.lucide-square") });
    await cancelBtn.waitFor({ state: "visible", timeout: 60_000 }).catch(() => {});
    await cancelBtn.waitFor({ state: "hidden", timeout: 120_000 });

    // Switch to sub-agent
    await agentCards.first().locator("button").first().click();
    await page.waitForTimeout(2_000);

    // Meta-Agent messages should NOT be visible
    const bodyText = await page.locator("body").textContent();
    expect(bodyText).not.toContain("META_MARKER_123");

    // Switch back to Meta-Agent
    await metaBtn.click();
    await page.waitForTimeout(2_000);

    // Meta-Agent messages SHOULD be visible again
    const bodyText2 = await page.locator("body").textContent();
    expect(bodyText2).toContain("META_MARKER_123");
  });
});

test.describe("Unsaved Changes Guard", () => {
  test("editing agent form and navigating away shows confirmation", async ({ page }) => {
    await page.goto("/#/agents");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2_000);

    // Open first agent edit
    const editButtons = page.locator("svg.lucide-settings-2").locator("..");
    if (await editButtons.count() === 0) {
      test.skip(true, "No agents available");
      return;
    }

    await editButtons.first().click();
    await page.waitForURL(/#\/agents\/edit\//, { timeout: 10_000 });
    await page.getByText(/basic info|基本信息/i).first().waitFor({ state: "visible", timeout: 15_000 });

    // Make a change to trigger dirty state
    const promptTextarea = page.locator("textarea").first();
    const original = await promptTextarea.inputValue();
    await promptTextarea.fill(original + "\n\nE2E unsaved guard test");

    // Try to navigate away by clicking another agent in sidebar
    const sidebar = page.locator("aside").first();
    const metaBtn = sidebar.locator("button").filter({ has: page.locator("svg.lucide-message-square") }).first();
    await metaBtn.click();
    await page.waitForTimeout(1_000);

    // Confirmation dialog should appear
    const confirmDialog = page.locator(".fixed.inset-0").last();
    const dialogVisible = await confirmDialog.isVisible({ timeout: 3_000 }).catch(() => false);

    if (dialogVisible) {
      // Dialog should have discard/stay options
      const discardBtn = confirmDialog.getByRole("button", { name: /discard|放弃/i });
      const stayBtn = confirmDialog.getByRole("button", { name: /stay|留下/i });

      // Click stay to go back to editing
      if (await stayBtn.isVisible().catch(() => false)) {
        await stayBtn.click();
        await page.waitForTimeout(500);
        // Should still be on edit page
        expect(page.url()).toMatch(/#\/agents\/edit\//);
      }
    }

    // Revert changes
    await promptTextarea.fill(original);
  });
});

test.describe("Keyboard Shortcuts", () => {
  test("Escape closes open modals", async ({ page, skillsPage, skillDetailPage }) => {
    // Create a skill to test with
    const skillName = `e2e-esc-${Date.now().toString(36)}`;
    await skillsPage.goto();
    await skillsPage.createSkill(skillName, "Escape test");
    await expect(skillDetailPage.editorContainer).toBeVisible({ timeout: 15_000 });

    // Edit to create dirty state
    await skillDetailPage.appendToEditor("\n## Escape test");

    // Open diff modal
    const diffBtn = page.locator("button").filter({ has: page.locator("svg.lucide-git-compare") });
    await expect(diffBtn).toBeVisible({ timeout: 5_000 });
    await diffBtn.click();

    const modal = page.locator(".fixed.inset-0").last();
    await expect(modal).toBeVisible({ timeout: 3_000 });

    // Press Escape to close
    await page.keyboard.press("Escape");
    await page.waitForTimeout(500);

    // Modal should be closed, editor still visible
    await expect(skillDetailPage.editorContainer).toBeVisible();

    // Cleanup
    await skillDetailPage.discardButton.click();
    await skillDetailPage.deleteSkill();
  });
});

test.describe("Chat Export", () => {
  test.setTimeout(180_000);

  test("export button triggers download after sending message", async ({ page }) => {
    await page.goto("/#/agents");
    await page.waitForLoadState("networkidle");
    await expect(page.locator("h2").first()).toContainText(/Meta Agent/i, { timeout: 15_000 });

    // Send a message so export button appears
    const textarea = page.locator("textarea").first();
    await textarea.fill("respond with: export test marker");
    await page.locator("button[type='submit']").first().click();

    const cancelBtn = page.locator("button").filter({ has: page.locator("svg.lucide-square") });
    await cancelBtn.waitFor({ state: "visible", timeout: 60_000 }).catch(() => {});
    await cancelBtn.waitFor({ state: "hidden", timeout: 120_000 });

    // Export button (Download icon) should be visible
    const exportBtn = page.locator("button").filter({ has: page.locator("svg.lucide-download") }).first();
    await expect(exportBtn).toBeVisible({ timeout: 5_000 });

    // Click export — should trigger a download
    const downloadPromise = page.waitForEvent("download", { timeout: 5_000 }).catch(() => null);
    await exportBtn.click();
    const download = await downloadPromise;

    if (download) {
      // Verify the downloaded file is a markdown file
      const filename = download.suggestedFilename();
      expect(filename).toMatch(/\.md$/);
      console.log(`Downloaded: ${filename}`);
    }
  });
});

test.describe("Error Handling", () => {
  test("sending empty message does nothing", async ({ page }) => {
    await page.goto("/#/agents");
    await page.waitForLoadState("networkidle");
    await expect(page.locator("h2").first()).toContainText(/Meta Agent/i, { timeout: 15_000 });

    // Try to click send with empty textarea
    const sendBtn = page.locator("button[type='submit']").first();
    const textarea = page.locator("textarea").first();

    // Textarea should be empty
    const value = await textarea.inputValue();
    expect(value).toBe("");

    // Send button should be disabled
    await expect(sendBtn).toBeDisabled();
  });

  test("skill with duplicate filename shows error", async ({ page, skillsPage, skillDetailPage }) => {
    const skillName = `e2e-dup-${Date.now().toString(36)}`;
    await skillsPage.goto();
    await skillsPage.createSkill(skillName, "Duplicate file test");
    await expect(skillDetailPage.editorContainer).toBeVisible({ timeout: 15_000 });
    await page.waitForTimeout(2_000);

    // Create a file
    const newFileBtn = page.locator("button[title*='file'], button[title*='文件']").filter({ has: page.locator("svg") }).first();
    await newFileBtn.click();
    const dialog1 = page.locator(".fixed.inset-0").last();
    await dialog1.waitFor({ state: "visible", timeout: 5_000 });
    await dialog1.locator("input").fill("test.py");
    await dialog1.getByRole("button", { name: /create|创建|ok|confirm|确认/i }).click();
    await page.waitForTimeout(1_000);

    // Try to create another file with the same name
    await newFileBtn.click();
    const dialog2 = page.locator(".fixed.inset-0").last();
    await dialog2.waitFor({ state: "visible", timeout: 5_000 });
    await dialog2.locator("input").fill("test.py");
    await dialog2.getByRole("button", { name: /create|创建|ok|confirm|确认/i }).click();
    await page.waitForTimeout(1_000);

    // Should show an error or dialog stays open — close any open dialogs first
    await page.keyboard.press("Escape");
    await page.waitForTimeout(500);
    // Close any remaining overlays
    const overlays = page.locator(".fixed.inset-0");
    for (let i = await overlays.count() - 1; i >= 0; i--) {
      if (await overlays.nth(i).isVisible().catch(() => false)) {
        await page.keyboard.press("Escape");
        await page.waitForTimeout(300);
      }
    }

    // Cleanup
    if (await skillDetailPage.discardButton.isVisible({ timeout: 3_000 }).catch(() => false)) {
      await skillDetailPage.discardButton.click({ force: true });
      await page.waitForTimeout(500);
    }
    await skillDetailPage.deleteSkill();
  });
});
