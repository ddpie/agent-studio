import type { Page, Locator } from "@playwright/test";

export class ChatPage {
  readonly page: Page;
  readonly heading: Locator;
  readonly textarea: Locator;
  readonly sendButton: Locator;
  readonly cancelButton: Locator;
  readonly newSessionButton: Locator;
  readonly historyButton: Locator;
  readonly modelButton: Locator;
  readonly messageList: Locator;

  constructor(page: Page) {
    this.page = page;
    this.heading = page.locator("h2").first();
    this.textarea = page.locator("textarea").first();
    this.sendButton = page.locator("button[type='submit']").first();
    this.cancelButton = page.locator("button").filter({ has: page.locator("svg.lucide-square") });
    this.newSessionButton = page.locator("button").filter({ has: page.locator("svg.lucide-plus") }).last();
    this.historyButton = page.locator("button").filter({ has: page.locator("svg.lucide-history") });
    this.modelButton = page.locator("button").filter({ hasText: /claude|Opus|Sonnet|Haiku|Nova/i }).first();
    this.messageList = page.locator(".flex-1.overflow-y-auto").first();
  }

  async goto(agentId?: string) {
    if (agentId) {
      await this.page.goto(`/#/agents/chat/${agentId}`);
    } else {
      await this.page.goto("/#/agents");
    }
    await this.page.waitForLoadState("networkidle");
  }

  async sendMessage(text: string) {
    await this.textarea.fill(text);
    await this.sendButton.click();
  }

  /**
   * Wait for an assistant response to appear (non-empty).
   * Streaming may take a while with real backend.
   */
  async waitForResponse(timeout = 120_000) {
    // Wait for at least one assistant message div to appear with content
    const assistantMsg = this.page.locator('[class*="message"], .prose, .react-markdown').last();
    await assistantMsg.waitFor({ state: "visible", timeout });
    // Wait for streaming to finish — send button reappears (cancel button disappears)
    await this.cancelButton.waitFor({ state: "hidden", timeout });
  }

  getAssistantMessages(): Locator {
    // Assistant messages are rendered with react-markdown
    return this.page.locator(".prose, .react-markdown");
  }

  getUserMessages(): Locator {
    // User messages contain the sent text
    return this.page.locator('[class*="bg-blue-"], [class*="bg-blue-50"]');
  }

  async newSession() {
    await this.newSessionButton.click();
    await this.page.waitForTimeout(500);
  }

  async openHistory() {
    await this.historyButton.click();
    await this.page.waitForTimeout(300);
  }

  getHistoryPanel(): Locator {
    return this.page.locator(".absolute.right-0.top-full").filter({ has: this.page.locator("svg.lucide-clock") });
  }

  getSessionItems(): Locator {
    return this.getHistoryPanel().locator("> div").filter({ has: this.page.locator("svg.lucide-clock") });
  }

  async deleteSession(index: number) {
    const sessions = this.getSessionItems();
    const deleteBtn = sessions.nth(index).locator("button").filter({ has: this.page.locator("svg.lucide-trash-2") });
    await deleteBtn.click({ force: true });
  }

  async openModelPicker() {
    await this.modelButton.click();
    await this.page.waitForTimeout(300);
  }
}
