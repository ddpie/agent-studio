import type { Page, Locator } from "@playwright/test";

export class ToolDetailPage {
  readonly page: Page;
  readonly toolName: Locator;
  readonly saveButton: Locator;
  readonly validateButton: Locator;
  readonly discardButton: Locator;
  readonly deleteButton: Locator;
  readonly backButton: Locator;
  readonly editorContainer: Locator;

  constructor(page: Page) {
    this.page = page;
    this.toolName = page.locator("h2").first();
    this.saveButton = page.getByRole("button", { name: /save|保存/i });
    // Validate button is in the toolbar — use .first() to avoid matching assistant panel buttons
    this.validateButton = page.getByRole("button", { name: /validate|验证/i }).first();
    this.discardButton = page.getByRole("button", { name: /discard|放弃/i });
    this.deleteButton = page.locator("button").filter({ has: page.locator("svg.lucide-trash-2") });
    this.backButton = page.locator("button").filter({ has: page.locator("svg.lucide-chevron-left") }).first();
    this.editorContainer = page.locator(".monaco-editor").first();
  }

  /**
   * Set code in the Monaco editor. For controlled @monaco-editor/react,
   * we must trigger the internal onChange by dispatching through the editor's
   * onDidChangeModelContent event. We do this by using the editor instance
   * directly and calling its trigger method.
   */
  async setCode(code: string) {
    await this.editorContainer.waitFor({ state: "visible" });
    await this.editorContainer.click();
    // Grant clipboard permissions and paste
    await this.page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
    await this.page.evaluate((text) => navigator.clipboard.writeText(text), code);
    // Select all then paste — this goes through Monaco's normal input pipeline
    await this.page.keyboard.press("Meta+a");
    await this.page.keyboard.press("Meta+v");
    await this.page.waitForTimeout(500);
  }

  async getCode(): Promise<string> {
    return this.page.evaluate(() => {
      const monaco = (window as any).monaco;
      if (!monaco) return "";
      const models = monaco.editor.getModels();
      if (models.length === 0) return "";
      return models[models.length - 1].getValue();
    });
  }

  async save() {
    await this.saveButton.click();
    // Wait for save to complete — saving spinner disappears
    await this.page.waitForTimeout(2_000);
  }

  async validate() {
    await this.validateButton.click();
    await this.page.locator("svg.lucide-loader-2.animate-spin").waitFor({ state: "hidden", timeout: 60_000 });
  }

  async deleteTool() {
    await this.deleteButton.click();
    const confirmDialog = this.page.locator(".fixed.inset-0").last();
    await confirmDialog.waitFor({ state: "visible" });
    await confirmDialog.getByRole("button", { name: /trash|回收站/i }).click();
    await this.page.waitForURL(/#\/tools/, { timeout: 10_000 });
  }

  async goBack() {
    await this.backButton.click();
    await this.page.waitForURL(/#\/tools/, { timeout: 10_000 });
  }
}
