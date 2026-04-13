import { test, expect } from "../fixtures/test";

test.describe("Chat Flow", () => {
  test.setTimeout(180_000); // streaming with real backend can be slow

  test("send message to Meta-Agent and receive streaming response", async ({ chatPage }) => {
    await chatPage.goto();

    // Verify Meta-Agent heading
    await expect(chatPage.heading).toContainText(/Meta Agent/i, { timeout: 15_000 });

    // Send a simple message
    await chatPage.sendMessage("say hello in one sentence");

    // Wait for response to complete
    await chatPage.waitForResponse();

    // Verify at least one assistant message appeared
    const assistantMsgs = chatPage.getAssistantMessages();
    const count = await assistantMsgs.count();
    expect(count).toBeGreaterThan(0);
  });

  test("session management — create new session clears chat", async ({ chatPage, page }) => {
    await chatPage.goto();
    await expect(chatPage.heading).toContainText(/Meta Agent/i, { timeout: 15_000 });

    // ── 1. Send a message in the default session ──
    await chatPage.sendMessage("respond with only: session-one-marker");
    await chatPage.waitForResponse();

    // Verify the response appeared
    const bodyText1 = await page.locator("body").textContent();
    expect(bodyText1).toContain("session-one-marker");

    // ── 2. Create a new session ──
    await chatPage.newSession();
    await page.waitForTimeout(1_000);

    // Welcome/empty state should reappear (messages cleared)
    const welcomeText = page.getByText(/欢迎|welcome/i);
    await expect(welcomeText).toBeVisible({ timeout: 5_000 });

    // ── 3. Open history — should have at least 1 saved session ──
    await chatPage.openHistory();
    const historyPanel = chatPage.getHistoryPanel();
    await expect(historyPanel).toBeVisible({ timeout: 5_000 });
  });

  test("new session button clears messages", async ({ chatPage, page }) => {
    await chatPage.goto();
    await expect(chatPage.heading).toContainText(/Meta Agent/i, { timeout: 15_000 });

    // Send a message
    await chatPage.sendMessage("respond with only: clear-test");
    await chatPage.waitForResponse();

    // Verify message exists
    const bodyText = await page.locator("body").textContent();
    expect(bodyText).toContain("clear-test");

    // Create new session
    await chatPage.newSession();
    await page.waitForTimeout(500);

    // Welcome/empty state should reappear
    const welcomeText = page.getByText(/欢迎|welcome/i);
    await expect(welcomeText).toBeVisible({ timeout: 5_000 });
  });

  test("cancel streaming stops response", async ({ chatPage, page }) => {
    await chatPage.goto();
    await expect(chatPage.heading).toContainText(/Meta Agent/i, { timeout: 15_000 });

    // Send a message that will produce a long response
    await chatPage.sendMessage("write a detailed 500-word essay about artificial intelligence");

    // Wait for streaming to start — cancel button (Square icon) should appear
    const cancelBtn = page.locator("button").filter({ has: page.locator("svg.lucide-square") });
    await expect(cancelBtn).toBeVisible({ timeout: 30_000 });

    // Click cancel
    await cancelBtn.click();

    // Send button should reappear (streaming stopped)
    const sendBtn = page.locator("button[type='submit']").first();
    await expect(sendBtn).toBeVisible({ timeout: 10_000 });

    // Textarea should be re-enabled
    await expect(chatPage.textarea).toBeEnabled({ timeout: 5_000 });
  });

  test("session persists in history and can be loaded", async ({ chatPage, page }) => {
    await chatPage.goto();
    await expect(chatPage.heading).toContainText(/Meta Agent/i, { timeout: 15_000 });

    // Send a unique marker message
    const marker = `marker-${Date.now().toString(36)}`;
    await chatPage.sendMessage(`respond with only: ${marker}`);
    await chatPage.waitForResponse();

    // Create a new session (old one gets saved)
    await chatPage.newSession();
    await page.waitForTimeout(1_000);

    // Open history
    await chatPage.openHistory();
    const historyPanel = chatPage.getHistoryPanel();
    await expect(historyPanel).toBeVisible({ timeout: 5_000 });

    // Click the saved session (should contain our marker in title)
    const sessionItem = historyPanel.locator("> div").filter({ has: page.locator("svg.lucide-clock") }).first();
    await sessionItem.click();
    await page.waitForTimeout(1_000);

    // The marker message should be visible again
    const bodyText = await page.locator("body").textContent();
    expect(bodyText).toContain(marker);
  });

  test("delete session from history", async ({ chatPage, page }) => {
    await chatPage.goto();
    await expect(chatPage.heading).toContainText(/Meta Agent/i, { timeout: 15_000 });

    // Send a message to create a session
    await chatPage.sendMessage("respond with only: delete-me-session");
    await chatPage.waitForResponse();

    // New session so the old one is saved
    await chatPage.newSession();
    await page.waitForTimeout(1_000);

    // Open history and count sessions
    await chatPage.openHistory();
    const historyPanel = chatPage.getHistoryPanel();
    await expect(historyPanel).toBeVisible({ timeout: 5_000 });

    const sessionsBefore = historyPanel.locator("> div").filter({ has: page.locator("svg.lucide-clock") });
    const countBefore = await sessionsBefore.count();
    expect(countBefore).toBeGreaterThan(0);

    // Delete the first session — hover to reveal trash icon
    const firstSession = sessionsBefore.first();
    await firstSession.hover();
    const deleteBtn = firstSession.locator("button").filter({ has: page.locator("svg.lucide-trash-2") });
    await deleteBtn.click({ force: true });
    await page.waitForTimeout(1_000);

    // Session count should decrease
    const countAfter = await sessionsBefore.count();
    expect(countAfter).toBeLessThan(countBefore);
  });
});
