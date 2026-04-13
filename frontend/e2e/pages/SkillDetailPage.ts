import type { Page, Locator } from "@playwright/test";

/**
 * Bilingual locators (zh + en) for the Skill detail/editor page.
 */
export class SkillDetailPage {
  readonly page: Page;
  readonly skillName: Locator;
  readonly saveButton: Locator;
  readonly validateButton: Locator;
  readonly discardButton: Locator;
  readonly deleteButton: Locator;
  readonly diffButton: Locator;
  readonly backButton: Locator;
  readonly fileTree: Locator;
  readonly editorContainer: Locator;

  constructor(page: Page) {
    this.page = page;
    this.skillName = page.locator("h2").first();
    this.saveButton = page.getByRole("button", { name: /save|保存/i });
    this.validateButton = page.getByRole("button", { name: /validate|验证/i });
    this.discardButton = page.getByRole("button", { name: /discard|放弃/i });
    this.deleteButton = page.locator("button").filter({ has: page.locator("svg.lucide-trash-2") });
    this.diffButton = page.getByRole("button", { name: /diff/i });
    this.backButton = page.locator("button").filter({ has: page.locator("svg.lucide-chevron-left") }).first();
    this.fileTree = page.locator("[data-testid='file-tree'], .react-arborist");
    this.editorContainer = page.locator(".monaco-editor").first();
  }

  /**
   * Type into Monaco editor via its API (keyboard.type is unreliable with Monaco).
   * Replaces all content.
   */
  async setEditorContent(text: string) {
    await this.editorContainer.waitFor({ state: "visible" });
    await this.page.evaluate((content) => {
      const monaco = (window as any).monaco;
      if (!monaco) throw new Error("Monaco not found on window");
      const models = monaco.editor.getModels();
      if (models.length === 0) throw new Error("No Monaco models found");
      const model = models[models.length - 1];
      model.setValue(content);
    }, text);
  }

  /**
   * Append text to the end of the Monaco editor content via API.
   */
  async appendToEditor(text: string) {
    await this.editorContainer.waitFor({ state: "visible" });
    await this.page.evaluate((appendText) => {
      const monaco = (window as any).monaco;
      if (!monaco) throw new Error("Monaco not found on window");
      const models = monaco.editor.getModels();
      if (models.length === 0) throw new Error("No Monaco models found");
      const model = models[models.length - 1];
      const currentValue = model.getValue();
      model.setValue(currentValue + "\n" + appendText);
    }, text);
  }

  /**
   * Get the current Monaco editor content via API.
   */
  async getEditorContent(): Promise<string> {
    await this.editorContainer.waitFor({ state: "visible" });
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
    // Wait for save to complete — button disappears when no pending ops
    await this.saveButton.waitFor({ state: "hidden", timeout: 15_000 });
  }

  async validate() {
    await this.validateButton.click();
    // Wait for validation to finish (spinner stops)
    await this.page.locator("svg.lucide-loader-2.animate-spin").waitFor({ state: "hidden", timeout: 60_000 });
  }

  async deleteSkill() {
    await this.deleteButton.click();
    // Confirm in the dialog — matches "Move to Trash" / "移到回收站"
    const confirmDialog = this.page.locator(".fixed.inset-0").last();
    await confirmDialog.waitFor({ state: "visible" });
    await confirmDialog.getByRole("button", { name: /trash|回收站/i }).click();
    // Should navigate back to skills list
    await this.page.waitForURL(/#\/skills/, { timeout: 10_000 });
  }

  async goBack() {
    await this.backButton.click();
    await this.page.waitForURL(/#\/skills/, { timeout: 10_000 });
  }

  /**
   * Check if the validation banner shows success or errors.
   */
  getValidationBanner(): Locator {
    return this.page.locator("[class*='ValidationBanner'], [role='alert']").first();
  }

  /**
   * Select a file in the file tree by its name.
   */
  async selectFile(fileName: string) {
    await this.page.getByText(fileName, { exact: false }).click();
    // Wait for editor to load the file content
    await this.page.waitForTimeout(500);
  }
}
