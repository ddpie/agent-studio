import type { Page, Locator } from "@playwright/test";

export class ToolsPage {
  readonly page: Page;
  readonly heading: Locator;
  readonly searchInput: Locator;
  readonly createButton: Locator;

  constructor(page: Page) {
    this.page = page;
    this.heading = page.locator("h2");
    this.searchInput = page.locator("input[type='text']").first();
    this.createButton = page.getByRole("button", { name: /create|创建/i });
  }

  async goto() {
    await this.page.goto("/#/tools");
    await this.page.waitForLoadState("networkidle");
  }

  async createTool(name: string, description?: string) {
    await this.createButton.click();

    const dialog = this.page.locator(".fixed.inset-0").last();
    await dialog.waitFor({ state: "visible" });

    const nameInput = dialog.locator("input").first();
    await nameInput.fill(name);

    if (description) {
      const descInput = dialog.locator("input").nth(1);
      await descInput.fill(description);
    }

    await dialog.getByRole("button", { name: /create|创建/i }).click();

    // Navigates to /tools/{id}?new=1&name=...
    await this.page.waitForURL(/#\/tools\//, { timeout: 15_000 });
  }

  async searchTool(query: string) {
    await this.searchInput.fill(query);
  }

  getToolCard(name: string): Locator {
    return this.page.locator(".grid > div").filter({ hasText: name });
  }

  async openTool(name: string) {
    await this.getToolCard(name).click();
    await this.page.waitForURL(/#\/tools\//, { timeout: 10_000 });
  }
}
