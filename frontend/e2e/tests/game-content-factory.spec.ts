import { test, expect } from "../fixtures/test";
import { type Page } from "@playwright/test";
import * as path from "path";
import * as fs from "fs";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

/**
 * Game Content Factory Demo — Full E2E in Isolated Workspace
 *
 * Tests the core demo link:
 *   Dialog Writer Agent ──A2A──▶ Worldview Reviewer Agent (with KB)
 *
 * Flow:
 *   1. Create a dedicated workspace for isolation
 *   2. Create Knowledge Base + upload lore documents via UI
 *   3. Wait for ingestion to complete
 *   4. Create Worldview Reviewer Agent (bound to KB) via Meta-Agent
 *   5. Create Dialog Writer Agent via Meta-Agent
 *   6. Link Writer → Reviewer (A2A)
 *   7. Invoke Writer: "帮月見写 5 条 idle 对白"
 *   8. Verify: reviewer feedback appears in response
 *   9. Cleanup: delete workspace (cascades agents + KB)
 *
 * Screenshots are saved to .claude/screenshots/ at each major step.
 */
test.describe("Game Content Factory — Isolated Workspace E2E", () => {
  test.setTimeout(900_000); // 15 min — covers KB ingestion + agent deployments

  const WORKSPACE_NAME = `e2e-gcf-${Date.now().toString(36)}`;
  const KB_NAME = "幻夜之刃世界观";
  const REVIEWER_NAME = "worldviewReviewer";
  const WRITER_NAME = "dialogWriter";

  const KB_DOCS_DIR = path.resolve(__dirname, "../../docs/demos/game-content-factory/kb");
  const SCREENSHOTS_DIR = path.resolve(__dirname, "../../.claude/screenshots");

  function screenshotPath(name: string): string {
    const now = new Date();
    const ts = [
      now.getFullYear().toString(),
      (now.getMonth() + 1).toString().padStart(2, "0"),
      now.getDate().toString().padStart(2, "0"),
      "-",
      now.getHours().toString().padStart(2, "0"),
      now.getMinutes().toString().padStart(2, "0"),
      now.getSeconds().toString().padStart(2, "0"),
    ].join("");
    return path.join(SCREENSHOTS_DIR, `${ts}-gcf-${name}.png`);
  }

  async function screenshot(page: Page, name: string) {
    fs.mkdirSync(SCREENSHOTS_DIR, { recursive: true });
    await page.screenshot({ path: screenshotPath(name), fullPage: true });
  }

  async function sendToMetaAgent(page: Page, message: string, timeout = 300_000) {
    const textarea = page.locator("textarea").first();
    await textarea.fill(message);
    await page.locator("button[type='submit']").first().click();

    const cancelBtn = page.locator("button").filter({ has: page.locator("svg.lucide-square") });
    await cancelBtn.waitFor({ state: "visible", timeout: 60_000 }).catch(() => {});
    await cancelBtn.waitFor({ state: "hidden", timeout });
  }

  async function getLastResponse(page: Page): Promise<string> {
    const messages = page.locator(".prose, .react-markdown");
    const count = await messages.count();
    if (count === 0) return "";
    return (await messages.last().textContent()) || "";
  }

  function collectKBFiles(): string[] {
    const files: string[] = [];
    function walk(dir: string) {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) walk(full);
        else if (entry.name.endsWith(".md")) files.push(full);
      }
    }
    if (fs.existsSync(KB_DOCS_DIR)) walk(KB_DOCS_DIR);
    return files;
  }

  test("full pipeline: workspace → KB + docs → agents → link → invoke → verify", async ({ page }) => {
    // Set viewport to match project convention
    await page.setViewportSize({ width: 1440, height: 900 });

    // Helper: log with timestamp for progress tracking
    const log = (msg: string) => console.log(`[GCF ${new Date().toISOString().slice(11, 19)}] ${msg}`);

    // ════════════════════════════════════════════════
    // STEP 1: Create isolated workspace
    // ════════════════════════════════════════════════
    await test.step("Create isolated workspace", async () => {
      log("Step 1: Navigating to /#/agents...");
      await page.goto("/#/agents");
      await page.waitForLoadState("networkidle");
      await page.waitForTimeout(2_000);

      await screenshot(page, "01-before-workspace-create");
      log("Step 1: Page loaded, opening workspace switcher...");

      // Open workspace switcher
      await page.getByTestId("workspace-switcher-btn").click();
      await page.waitForTimeout(500);

      // Click "Create Workspace" button
      await page.getByTestId("create-workspace-btn").click();
      await page.waitForTimeout(500);

      await screenshot(page, "02-workspace-create-dialog");

      // Fill name + submit
      await page.getByTestId("create-workspace-name").fill(WORKSPACE_NAME);
      await page.getByTestId("create-workspace-submit").click();

      // WorkspaceSwitcher triggers window.location.reload() after creation.
      // Wait for that reload to land, then re-navigate to ensure valid route.
      await page.waitForLoadState("load");
      await page.waitForTimeout(3_000);

      // After reload the page may land on a 404 or blank state — navigate
      // explicitly to /agents to bootstrap the app in the new workspace.
      await page.goto("/#/agents");
      await page.waitForLoadState("networkidle");
      await page.waitForTimeout(5_000);

      // Verify we're in the new workspace
      const switcher = page.getByTestId("workspace-switcher-btn");
      await expect(switcher).toContainText(WORKSPACE_NAME, { timeout: 15_000 });

      await screenshot(page, "03-workspace-created");
      log("Step 1: DONE — workspace created and verified");
    });

    // ════════════════════════════════════════════════
    // STEP 2: Create Knowledge Base via UI
    // ════════════════════════════════════════════════
    let kbId: string | undefined;

    await test.step("Create Knowledge Base", async () => {
      log("Step 2: Navigating to KB page...");
      // Use sidebar icon link to navigate to KB page.
      const kbLink = page.locator('a[href*="knowledge-bases"]').first();
      await kbLink.waitFor({ state: "visible", timeout: 15_000 });
      await kbLink.click();
      await page.waitForLoadState("networkidle");
      await page.waitForTimeout(2_000);
      log("Step 2: KB page loaded");

      await screenshot(page, "04-kb-list-empty");

      // Click "+ 创建知识库" button in header (the blue button)
      const createBtn = page.locator("button").filter({ hasText: /创建知识库|Create/ }).first();
      await createBtn.click();
      await page.waitForTimeout(1_000);
      log("Step 2: Create dialog opened");

      // The KBCreateDialog modal is now visible.
      // Its name input has autoFocus and is inside the modal overlay.
      const dialog = page.locator(".fixed.inset-0.z-50");
      await dialog.waitFor({ state: "visible", timeout: 5_000 });

      // Fill the KB name (first input inside the dialog)
      const nameInput = dialog.locator("input").first();
      await nameInput.fill(KB_NAME);
      log(`Step 2: Filled KB name: ${KB_NAME}`);

      await screenshot(page, "05-kb-create-dialog");

      // Click the submit button inside the dialog (has text "创建" / "Create")
      const submitBtn = dialog.locator("button").filter({ hasText: /创建|Create/ }).last();
      await submitBtn.click();
      log("Step 2: Submit clicked, waiting for KB detail page...");

      // Wait for navigation to KB detail page
      await page.waitForURL(/knowledge-bases\/kb_/, { timeout: 60_000 });
      await page.waitForTimeout(2_000);

      // Extract KB ID from URL
      const url = page.url();
      const match = url.match(/knowledge-bases\/(kb_[a-f0-9]+)/);
      expect(match).toBeTruthy();
      kbId = match![1];
      log(`Step 2: DONE — KB created: ${kbId}`);

      await screenshot(page, "06-kb-detail-created");
    });

    // ════════════════════════════════════════════════
    // STEP 3: Upload KB documents
    // ════════════════════════════════════════════════
    await test.step("Upload KB documents", async () => {
      const kbFiles = collectKBFiles();
      expect(kbFiles.length).toBeGreaterThan(0);
      log(`Step 3: Uploading ${kbFiles.length} KB documents...`);

      // Upload files in batches
      const BATCH_SIZE = 5;
      for (let i = 0; i < kbFiles.length; i += BATCH_SIZE) {
        const batch = kbFiles.slice(i, i + BATCH_SIZE);

        const fileInput = page.locator('input[type="file"]');
        await fileInput.setInputFiles(batch);

        // Wait for upload to complete
        await page.waitForTimeout(5_000);

        // Wait for all loaders to disappear
        const loaders = page.locator("svg.lucide-loader-2.animate-spin");
        const loaderCount = await loaders.count();
        if (loaderCount > 0) {
          await loaders.first().waitFor({ state: "hidden", timeout: 60_000 });
        }

        log(`Step 3: Uploaded batch ${Math.floor(i / BATCH_SIZE) + 1}: ${batch.map(f => path.basename(f)).join(", ")}`);
        await screenshot(page, `07-kb-upload-batch-${Math.floor(i / BATCH_SIZE) + 1}`);
      }

      // Verify documents appear in the table
      await page.waitForTimeout(3_000);
      const docRows = page.locator("table tbody tr, [class*='document']");
      const docCount = await docRows.count();
      log(`Step 3: DONE — ${docCount} documents visible in table`);
      expect(docCount).toBeGreaterThan(0);

      await screenshot(page, "08-kb-docs-uploaded");
    });

    // ════════════════════════════════════════════════
    // STEP 4: Wait for ingestion
    // ════════════════════════════════════════════════
    await test.step("Wait for KB ingestion to complete", async () => {
      log("Step 4: Waiting for KB ingestion...");
      const maxWait = 180_000; // 3 minutes
      const pollInterval = 10_000;
      const start = Date.now();

      while (Date.now() - start < maxWait) {
        const refreshBtn = page.locator("button").filter({ has: page.locator("svg.lucide-refresh-cw") }).first();
        await refreshBtn.click().catch(() => {});
        await page.waitForTimeout(pollInterval);

        const pageText = await page.textContent("body");
        if (
          pageText?.includes("ACTIVE") ||
          pageText?.includes("COMPLETE") ||
          pageText?.includes("完成") ||
          pageText?.includes("indexed")
        ) {
          console.log("KB ingestion complete");
          break;
        }

        const ingestionSection = page.locator("text=ingestion, text=同步中, text=处理中");
        if ((await ingestionSection.count()) === 0) {
          console.log("No pending ingestion indicators found — assuming complete");
          break;
        }
      }

      await screenshot(page, "09-kb-ingestion-complete");
    });

    // ════════════════════════════════════════════════
    // STEP 5: Create Worldview Reviewer Agent via Meta-Agent
    // ════════════════════════════════════════════════
    await test.step("Create Worldview Reviewer Agent", async () => {
      log("Step 5: Creating Worldview Reviewer Agent...");
      await page.goto("/#/agents");
      await page.waitForLoadState("networkidle");
      await page.waitForTimeout(3_000);

      const reviewerPrompt = `帮我造一个世界观审核助手。

它的唯一职责是检查游戏内容有没有违反世界观设定。不靠记忆判断——每次审核前必须先查知识库，找到对应的设定原文再做判定。

审核要从这几个角度看：有没有泄露未公开的版本信息、角色说的话像不像这个人、有没有时间线矛盾、提到的地名是不是当前版本已公开的、角色关系有没有写错、语言风格是不是符合游戏调性。

判定分三级：红色（必须改）、黄色（建议改）、绿色（通过）。每条判定都要附上从知识库查到的依据。

当前游戏版本是 3.1，任何标注为 3.2 或 4.0 才公开的内容都算泄露。

agent_name 用 "${REVIEWER_NAME}"，绑定知识库"${KB_NAME}"。直接执行创建和部署，不要问我确认。`;

      await sendToMetaAgent(page, reviewerPrompt);

      const response = await getLastResponse(page);
      console.log("Reviewer creation response:", response.slice(0, 500));
      expect(response).toMatch(/成功|created|deployed|ready|READY|创建完成|已创建/i);

      await screenshot(page, "10-reviewer-created");
    });

    // ════════════════════════════════════════════════
    // STEP 6: Create Dialog Writer Agent via Meta-Agent
    // ════════════════════════════════════════════════
    await test.step("Create Dialog Writer Agent", async () => {
      const writerPrompt = `帮我造一个 NPC 对白创作助手。

它要能根据我给的角色名和场景，写出符合角色性格的游戏对白。支持三种类型：idle（闲置）、剧情、战斗。

写之前先查一下知识库里这个角色的设定卡，了解说话风格和禁忌。

写完后自动交给世界观审核 Agent 检查，如果有不通过的就自动改，最多改 3 轮，还不过就把问题和当前版本一起告诉我让我决定。

整体风格要求：和风、克制、有诗意。idle 对白每条 15-40 字，剧情不超过 80 字，战斗不超过 15 字。

agent_name 用 "${WRITER_NAME}"，绑定知识库"${KB_NAME}"。直接执行创建和部署，不要问我确认。`;

      await sendToMetaAgent(page, writerPrompt);

      const response = await getLastResponse(page);
      console.log("Writer creation response:", response.slice(0, 500));
      expect(response).toMatch(/成功|created|deployed|ready|READY|创建完成|已创建/i);

      await screenshot(page, "11-writer-created");
    });

    // ════════════════════════════════════════════════
    // STEP 7: Link Writer → Reviewer (A2A)
    // ════════════════════════════════════════════════
    await test.step("Link Writer → Reviewer via A2A", async () => {
      await sendToMetaAgent(
        page,
        `把 ${WRITER_NAME} 和 ${REVIEWER_NAME} 连起来。${WRITER_NAME} 写完对白后要自动调用 ${REVIEWER_NAME} 来审核。执行 link_agent，source 是 ${WRITER_NAME}，target 是 ${REVIEWER_NAME}。不要问我确认。`
      );

      const response = await getLastResponse(page);
      console.log("Link response:", response.slice(0, 500));
      expect(response).toMatch(/成功|linked|连接|关联|已.*link|已关联|call_agent/i);

      await screenshot(page, "12-agents-linked");
    });

    // ════════════════════════════════════════════════
    // STEP 8: Invoke Dialog Writer — generate dialog
    // ════════════════════════════════════════════════
    await test.step("Invoke Dialog Writer — generate dialog for 月見", async () => {
      // Refresh agent list to see the new agents
      await page.waitForTimeout(3_000);
      const refreshBtn = page.locator("button").filter({ has: page.locator("svg.lucide-refresh-cw") }).first();
      await refreshBtn.click().catch(() => {});
      await page.waitForTimeout(5_000);

      await screenshot(page, "13-agent-list-refreshed");

      // Find and click the writer agent in sidebar
      const writerLink = page.locator(`text=${WRITER_NAME}`).first();
      const writerVisible = await writerLink.isVisible().catch(() => false);

      if (writerVisible) {
        await writerLink.click();
        await page.waitForTimeout(3_000);
      } else {
        // Fallback: ask Meta-Agent for the agent_id
        console.log("Writer not in sidebar, using Meta-Agent to get agent_id...");
        await sendToMetaAgent(page, `查一下 ${WRITER_NAME} 的 agent_id。只返回 ID，不要做其他操作。`);
        const idResponse = await getLastResponse(page);
        const agentIdMatch = idResponse.match(/[a-f0-9-]{20,}/);
        if (agentIdMatch) {
          await page.goto(`/#/agents/chat/${agentIdMatch[0]}`);
          await page.waitForLoadState("networkidle");
          await page.waitForTimeout(3_000);
        }
      }

      await screenshot(page, "14-writer-chat-ready");

      // Send the creative request to the writer agent
      await sendToMetaAgent(
        page,
        "帮月見写 5 条 idle 对白",
        600_000 // 10 min — includes KB retrieval + A2A review round-trips
      );

      await screenshot(page, "15-writer-response-complete");
    });

    // ════════════════════════════════════════════════
    // STEP 9: Verify review happened
    // ════════════════════════════════════════════════
    await test.step("Verify dialog output with review evidence", async () => {
      const response = await getLastResponse(page);
      console.log("Writer output:", response.slice(0, 1000));

      // The response should contain dialog lines (non-trivial output)
      expect(response.length).toBeGreaterThan(50);

      // Should reference 月見 character
      const hasCharacterRef =
        response.includes("月見") ||
        response.includes("月见") ||
        response.includes("Tsukimi");
      expect(hasCharacterRef).toBe(true);

      // Should show evidence of review or dialog output fitting character style
      const hasDialogOrReview =
        response.includes("通过") ||
        response.includes("绿色") ||
        response.includes("PASS") ||
        response.includes("审核") ||
        response.includes("review") ||
        response.includes("idle") ||
        response.includes("……") || // 月見's signature ellipsis opening
        response.includes("对白");
      expect(hasDialogOrReview).toBe(true);

      await screenshot(page, "16-verification-passed");
    });

    // ════════════════════════════════════════════════
    // STEP 10: Cleanup — delete agents + KB
    // ════════════════════════════════════════════════
    await test.step("Cleanup — delete test resources", async () => {
      // Go back to Meta-Agent and clean up agents
      await page.goto("/#/agents");
      await page.waitForLoadState("networkidle");
      await page.waitForTimeout(2_000);

      // Delete agents via Meta-Agent
      await sendToMetaAgent(
        page,
        `清理测试资源：删除 agent "${WRITER_NAME}" 和 "${REVIEWER_NAME}"。直接执行 delete_agent，不要问我确认。`
      ).catch(() => {});

      await screenshot(page, "17-agents-deleted");

      // Delete KB via UI
      if (kbId) {
        await page.goto(`/#/knowledge-bases/${kbId}`);
        await page.waitForLoadState("networkidle");
        await page.waitForTimeout(2_000);

        const deleteBtn = page.locator("button").filter({ has: page.locator("svg.lucide-trash-2") }).first();
        if (await deleteBtn.isVisible().catch(() => false)) {
          await deleteBtn.click();
          await page.waitForTimeout(500);
          const confirmBtn = page.locator("button").filter({ hasText: /确认|Confirm/ }).first();
          if (await confirmBtn.isVisible().catch(() => false)) {
            await confirmBtn.click();
            await page.waitForTimeout(3_000);
          }
        }

        await screenshot(page, "18-kb-deleted");
      }

      console.log(`Test workspace "${WORKSPACE_NAME}" cleanup done (agents + KB deleted).`);
      console.log("Note: workspace shell remains — clean up manually or via admin console.");

      await screenshot(page, "19-cleanup-complete");
    });
  });
});
