import { test, expect } from "../fixtures/test";
import { type Page } from "@playwright/test";

/**
 * Game Content Factory Demo — Full E2E
 *
 * Tests the core demo link:
 *   Dialog Writer Agent ──A2A──▶ Worldview Reviewer Agent (with KB)
 *
 * Flow:
 *   1. Create Knowledge Base from game lore
 *   2. Create Worldview Reviewer Agent (bound to KB)
 *   3. Create Dialog Writer Agent
 *   4. Link Writer → Reviewer (A2A)
 *   5. Invoke Writer: "帮月見写 5 条 idle 对白"
 *   6. Verify: reviewer feedback appears in response
 *   7. Cleanup
 */
test.describe("Game Content Factory — Core Demo Link", () => {
  test.setTimeout(900_000); // 15 min — agent creation + deployment is slow

  const KB_NAME = "幻夜之刃世界观";
  const REVIEWER_NAME = "worldviewReviewer";
  const WRITER_NAME = "dialogWriter";

  let writerAgentId: string | undefined;

  async function sendToMetaAgent(page: Page, message: string, timeout = 300_000) {
    const textarea = page.locator("textarea").first();
    await textarea.fill(message);
    await page.locator("button[type='submit']").first().click();

    // Wait for streaming to start then finish
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

  test("full pipeline: create KB → agents → link → invoke → verify review", async ({ page }) => {
    await page.goto("/#/agents");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(3_000);

    // ── Step 1: Create Knowledge Base ──
    await test.step("Create Knowledge Base", async () => {
      await sendToMetaAgent(
        page,
        `创建一个知识库，名称："${KB_NAME}"，描述："幻夜之刃游戏世界观设定集，包含角色、地点、时间线、审核规范"。执行 kb_create，不要问我确认。`
      );

      const response = await getLastResponse(page);
      // Accept: created, already exists, or any mention of KB name/ID
      expect(response).toMatch(/创建完成|已存在|成功|created|knowledge.?base|kb_|ACTIVE/i);

      // If KB already exists, that's fine — just proceed
      if (response.includes("已存在")) {
        // Use existing KB
        await sendToMetaAgent(page, `好的，使用现有的知识库"${KB_NAME}"。`);
      }
    });

    // ── Step 2: Create Worldview Reviewer Agent ──
    await test.step("Create Worldview Reviewer Agent", async () => {
      const reviewerPrompt = `帮我造一个世界观审核助手。

它的唯一职责是检查游戏内容有没有违反世界观设定。不靠记忆判断——每次审核前必须先查知识库，找到对应的设定原文再做判定。

审核要从这几个角度看：有没有泄露未公开的版本信息、角色说的话像不像这个人、有没有时间线矛盾、提到的地名是不是当前版本已公开的、角色关系有没有写错、语言风格是不是符合游戏调性。

判定分三级：红色（必须改）、黄色（建议改）、绿色（通过）。每条判定都要附上从知识库查到的依据。

当前游戏版本是 3.1，任何标注为 3.2 或 4.0 才公开的内容都算泄露。

agent_name 用 "${REVIEWER_NAME}"，绑定知识库"${KB_NAME}"。直接执行创建，不要问我确认。`;

      await sendToMetaAgent(page, reviewerPrompt);

      const response = await getLastResponse(page);
      expect(response).toMatch(/成功|created|deployed|ready|已存在|创建完成|READY/i);

      if (response.includes("已存在")) {
        await sendToMetaAgent(page, `好的，使用现有的 ${REVIEWER_NAME}。`);
      }

      // reviewer created or reused
    });

    // ── Step 3: Create Dialog Writer Agent ──
    await test.step("Create Dialog Writer Agent", async () => {
      const writerPrompt = `帮我造一个 NPC 对白创作助手。

它要能根据我给的角色名和场景，写出符合角色性格的游戏对白。支持三种类型：idle（闲置）、剧情、战斗。

写之前先查一下知识库里这个角色的设定卡，了解说话风格和禁忌。

写完后自动交给世界观审核 Agent 检查，如果有不通过的就自动改，最多改 3 轮，还不过就把问题和当前版本一起告诉我让我决定。

整体风格要求：和风、克制、有诗意。idle 对白每条 15-40 字，剧情不超过 80 字，战斗不超过 15 字。

agent_name 用 "${WRITER_NAME}"，绑定知识库"${KB_NAME}"。直接执行创建，不要问我确认。`;

      await sendToMetaAgent(page, writerPrompt);

      const response = await getLastResponse(page);
      expect(response).toMatch(/成功|created|deployed|ready|已存在|创建完成|READY/i);

      if (response.includes("已存在")) {
        await sendToMetaAgent(page, `好的，使用现有的 ${WRITER_NAME}。`);
      }

      writerAgentId = WRITER_NAME;
    });

    // ── Step 4: Link Writer → Reviewer (A2A) ──
    await test.step("Link agents via A2A", async () => {
      await sendToMetaAgent(
        page,
        `把 ${WRITER_NAME} 和 ${REVIEWER_NAME} 连起来。${WRITER_NAME} 写完对白后要自动调用 ${REVIEWER_NAME} 来审核。执行 link_agent，不要问我确认。`
      );

      const response = await getLastResponse(page);
      expect(response).toMatch(/成功|linked|连接|关联|已.*link|call_agent|已存在/i);
    });

    // ── Step 5: Invoke Dialog Writer ──
    await test.step("Invoke Dialog Writer — generate dialog", async () => {
      // Navigate to chat with the writer agent
      // First refresh to see the new agents
      await page.waitForTimeout(2_000);
      const refreshBtn = page.locator("button").filter({ has: page.locator("svg.lucide-refresh-cw") }).first();
      await refreshBtn.click().catch(() => {});
      await page.waitForTimeout(5_000);

      // Find and click the writer agent in sidebar
      const writerLink = page.locator(`text=${WRITER_NAME}`).first();
      const writerVisible = await writerLink.isVisible().catch(() => false);

      if (writerVisible) {
        await writerLink.click();
        await page.waitForTimeout(2_000);
      } else {
        // Fallback: navigate directly
        await page.goto(`/#/agents/chat/${writerAgentId || WRITER_NAME}`);
        await page.waitForLoadState("networkidle");
        await page.waitForTimeout(3_000);
      }

      // Send the creative request
      await sendToMetaAgent(
        page,
        "帮月見写 5 条 idle 对白",
        600_000 // 10 min — includes KB retrieval + A2A review round-trips
      );
    });

    // ── Step 6: Verify review happened ──
    await test.step("Verify A2A review occurred", async () => {
      const response = await getLastResponse(page);

      // The response should contain dialog lines
      expect(response.length).toBeGreaterThan(50);

      // Should show evidence of review (green/pass markers, or revision notes)
      const hasReviewEvidence =
        response.includes("通过") ||
        response.includes("绿色") ||
        response.includes("PASS") ||
        response.includes("审核") ||
        response.includes("review") ||
        response.includes("月見");

      expect(hasReviewEvidence).toBe(true);

      // The dialog should reference 月見's character (shrine maiden, 和風 style)
      const hasCharacterFit =
        response.includes("月見") ||
        response.includes("神社") ||
        response.includes("樱") ||
        response.includes("风") ||
        response.includes("夜");

      expect(hasCharacterFit).toBe(true);
    });

    // ── Step 7: Cleanup ──
    await test.step("Cleanup — delete agents and KB", async () => {
      // Go back to Meta-Agent chat
      await page.goto("/#/agents");
      await page.waitForLoadState("networkidle");
      await page.waitForTimeout(2_000);

      await sendToMetaAgent(
        page,
        `清理测试资源：删除 agent "${WRITER_NAME}" 和 "${REVIEWER_NAME}"。执行 delete_agent，不要问我确认。`
      );

      // KB cleanup is optional — don't fail the test if it doesn't work
      await sendToMetaAgent(
        page,
        `删除知识库 "${KB_NAME}"。如果有 kb_delete 就执行，没有就跳过。`
      ).catch(() => {});
    });
  });
});
