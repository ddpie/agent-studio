import { test as base } from "@playwright/test";
import { SkillsPage } from "../pages/SkillsPage";
import { SkillDetailPage } from "../pages/SkillDetailPage";
import { ToolsPage } from "../pages/ToolsPage";
import { ToolDetailPage } from "../pages/ToolDetailPage";
import { ChatPage } from "../pages/ChatPage";

type Fixtures = {
  skillsPage: SkillsPage;
  skillDetailPage: SkillDetailPage;
  toolsPage: ToolsPage;
  toolDetailPage: ToolDetailPage;
  chatPage: ChatPage;
};

export const test = base.extend<Fixtures>({
  skillsPage: async ({ page }, use) => {
    await use(new SkillsPage(page));
  },
  skillDetailPage: async ({ page }, use) => {
    await use(new SkillDetailPage(page));
  },
  toolsPage: async ({ page }, use) => {
    await use(new ToolsPage(page));
  },
  toolDetailPage: async ({ page }, use) => {
    await use(new ToolDetailPage(page));
  },
  chatPage: async ({ page }, use) => {
    await use(new ChatPage(page));
  },
});

export { expect } from "@playwright/test";
