import type { Page, Locator } from "@playwright/test";

/**
 * Bilingual locator patterns (zh + en) since the UI language depends on user settings.
 */
export class SkillsPage {
  readonly page: Page;
  readonly heading: Locator;
  readonly searchInput: Locator;
  readonly createButton: Locator;
  readonly refreshButton: Locator;
  readonly trashButton: Locator;

  constructor(page: Page) {
    this.page = page;
    this.heading = page.locator("h2");
    this.searchInput = page.locator("input[type='text']").first();
    // Match both "创建" and "Create"
    this.createButton = page.getByRole("button", { name: /create|创建/i });
    this.refreshButton = page.locator("button").filter({ has: page.locator("svg.lucide-refresh-cw, svg.lucide-loader") });
    this.trashButton = page.locator("button").filter({ has: page.locator("svg.lucide-trash-2") }).first();
  }

  async goto() {
    await this.page.goto("/#/skills");
    await this.page.waitForLoadState("networkidle");
  }

  async createSkill(name: string, description?: string) {
    await this.createButton.click();

    // Dialog overlay
    const dialog = this.page.locator(".fixed.inset-0").last();
    await dialog.waitFor({ state: "visible" });

    const nameInput = dialog.locator("input").first();
    await nameInput.fill(name);

    if (description) {
      const descInput = dialog.locator("input").nth(1);
      await descInput.fill(description);
    }

    // Click the create/创建 button inside the dialog
    await dialog.getByRole("button", { name: /create|创建/i }).click();

    // Should navigate to skill detail page
    await this.page.waitForURL(/#\/skills\//, { timeout: 15_000 });
  }

  async searchSkill(query: string) {
    await this.searchInput.fill(query);
  }

  getSkillCard(name: string): Locator {
    return this.page.locator(".grid > div").filter({ hasText: name });
  }

  async openSkill(name: string) {
    await this.getSkillCard(name).click();
    await this.page.waitForURL(/#\/skills\//, { timeout: 10_000 });
  }
}
