import { test, expect } from "../fixtures/test";
import { type Page } from "@playwright/test";
import * as path from "path";
import * as fs from "fs";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

/**
 * Game Content Factory Demo — Natural Language E2E
 *
 * 全程通过与 Meta-Agent 对话驱动，模拟策划真实使用场景。
 * 在独立 workspace 中执行，避免污染。
 *
 * Flow:
 *   1. Create workspace
 *   2. Tell Meta-Agent to create KB + upload docs + create agents + link
 *   3. Chat with Writer Agent → verify output
 *   4. Cleanup
 */
test.describe("Game Content Factory — Chat-Driven E2E", () => {
  test.setTimeout(900_000); // 15 min

  const SCREENSHOTS_DIR = path.resolve(__dirname, "../../.claude/screenshots");

  const log = (msg: string) => console.log(`[GCF ${new Date().toISOString().slice(11, 19)}] ${msg}`);

  function screenshotPath(name: string): string {
    const now = new Date();
    const ts = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, "0")}${String(now.getDate()).padStart(2, "0")}-${String(now.getHours()).padStart(2, "0")}${String(now.getMinutes()).padStart(2, "0")}${String(now.getSeconds()).padStart(2, "0")}`;
    return path.join(SCREENSHOTS_DIR, `${ts}-gcf-${name}.png`);
  }

  async function screenshot(page: Page, name: string) {
    fs.mkdirSync(SCREENSHOTS_DIR, { recursive: true });
    await page.screenshot({ path: screenshotPath(name), fullPage: true });
    log(`Screenshot: ${name}`);
  }

  async function sendMessage(page: Page, message: string, timeout = 300_000) {
    const textarea = page.locator("textarea").first();
    await textarea.fill(message);
    await page.locator("button[type='submit']").first().click();

    // Wait for streaming to start (cancel button appears) then finish (disappears)
    const cancelBtn = page.locator("button").filter({ has: page.locator("svg.lucide-square") });
    // Must see the cancel button — if it doesn't appear, streaming never started
    await cancelBtn.waitFor({ state: "visible", timeout: 60_000 });
    await cancelBtn.waitFor({ state: "hidden", timeout });
  }

  async function getLastResponse(page: Page): Promise<string> {
    const messages = page.locator(".prose, .react-markdown");
    const count = await messages.count();
    if (count === 0) return "";
    return (await messages.last().textContent()) || "";
  }

  test("chat-driven: workspace → KB → agents → link → invoke → verify", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });

    // ════════════════════════════════════════════════
    // STEP 1: Navigate to Meta-Agent chat
    // ════════════════════════════════════════════════
    await test.step("Navigate to Meta-Agent", async () => {
      log("Step 1: Navigating to Meta-Agent chat...");
      await page.goto("/#/agents");
      await page.waitForLoadState("networkidle");
      await page.waitForTimeout(3_000);

      // Verify Meta-Agent is ready (heading visible)
      await expect(page.locator("h2").first()).toContainText(/Meta Agent/i, { timeout: 15_000 });

      await screenshot(page, "01-meta-agent-ready");
      log("Step 1: DONE — Meta-Agent ready");
    });

    // ════════════════════════════════════════════════
    // STEP 2: Create KB via Meta-Agent
    // ════════════════════════════════════════════════
    await test.step("Create Knowledge Base via Meta-Agent", async () => {
      log("Step 2: Asking Meta-Agent to create KB...");

      await sendMessage(
        page,
        `创建一个知识库，名称："幻夜之刃世界观"，描述："游戏世界观设定集，包含角色、地点、时间线"。直接执行 kb_create，不要问我确认。`
      );

      const response = await getLastResponse(page);
      log(`Step 2: Response (first 300 chars): ${response.slice(0, 300)}`);
      await screenshot(page, "03-kb-created");

      expect(response).toMatch(/创建|created|kb_|knowledge|成功|ACTIVE|已存在/i);
      log("Step 2: DONE");
    });

    // ════════════════════════════════════════════════
    // STEP 2.5: Upload KB documents via chat attachment
    // ════════════════════════════════════════════════
    await test.step("Upload KB documents via chat attachment", async () => {
      log("Step 2.5: Uploading KB docs via chat attachment...");

      // Key docs for the demo: 月見 character + worldview + dialog style guide
      const KB_DOCS_DIR = path.resolve(__dirname, "../../../docs/demos/game-content-factory/kb");
      const docsToUpload = [
        path.join(KB_DOCS_DIR, "characters/月见.md"),
        path.join(KB_DOCS_DIR, "world/世界概述.md"),
        path.join(KB_DOCS_DIR, "guidelines/对白风格指南.md"),
        path.join(KB_DOCS_DIR, "guidelines/审核红线总表.md"),
        path.join(KB_DOCS_DIR, "timeline/纪元与大事件.md"),
      ].filter(f => fs.existsSync(f));

      log(`Step 2.5: Found ${docsToUpload.length} docs to upload`);

      // Upload docs to S3 staging via page.evaluate (calls frontend API client)
      // then tell Meta-Agent the staging keys so it can call kb_upload_document.
      const stagingKeys: Array<{ filename: string; key: string }> = [];

      // Sanitize filenames for the upload API (must be [a-zA-Z0-9._-]+)
      const sanitizeFilename = (name: string) =>
        name.replace(/[^a-zA-Z0-9._-]/g, "_");

      for (const filePath of docsToUpload) {
        const originalFilename = path.basename(filePath);
        const filename = sanitizeFilename(originalFilename);
        const content = fs.readFileSync(filePath, "utf-8");

        const key = await page.evaluate(async ({ filename, content }) => {
          // Get JWT token from Amplify's localStorage cache
          const keys = Object.keys(localStorage);
          const idTokenKey = keys.find(k => k.includes("idToken"));
          const token = idTokenKey ? localStorage.getItem(idTokenKey) || "" : "";
          const wsId = localStorage.getItem("agent-studio-workspace-id") || "";

          if (!token || !wsId) {
            console.error(`[GCF-staging] token=${!!token} wsId=${wsId} keys=${keys.filter(k => k.includes('Token')).join(',')}`);
            return `ERR:no_auth token=${!!token} wsId=${!!wsId}`;
          }

          // API uses relative paths (same origin) in production/dev
          const apiUrl = window.location.origin;

          // Get presigned upload URL
          const resp = await fetch(`${apiUrl}/api/workspaces/${wsId}/uploads/attachments`, {
            method: "POST",
            headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
            body: JSON.stringify({ filename, content_type: "text/markdown", sessionId: "kb-e2e-upload" }),
          });
          if (!resp.ok) {
            const body = await resp.text().catch(() => "");
            return `ERR:api_${resp.status}:${body.slice(0, 100)}`;
          }
          const { uploadUrl, fields, s3Key } = await resp.json();

          // Upload content as file via presigned POST
          const blob = new Blob([content], { type: "text/markdown" });
          const formData = new FormData();
          for (const [k, v] of Object.entries(fields as Record<string, string>)) {
            formData.append(k, v);
          }
          formData.append("file", blob, filename);
          const uploadResp = await fetch(uploadUrl, { method: "POST", body: formData });
          if (!uploadResp.ok) return `ERR:upload_${uploadResp.status}`;

          return s3Key;
        }, { filename, content });

        if (key && !key.startsWith("ERR:")) {
          stagingKeys.push({ filename: originalFilename, key });
          log(`Step 2.5: Staged ${originalFilename} → ${key}`);
        } else {
          log(`Step 2.5: WARN — failed to stage ${originalFilename}: ${key || "null"}`);
        }
      }

      await screenshot(page, "02b-files-staged");
      log(`Step 2.5: ${stagingKeys.length} files staged to S3`);

      // Now tell Meta-Agent to upload each staged file to the KB
      const uploadInstructions = stagingKeys
        .map(({ filename, key }) => `- filename: "${filename}", staging_key: "${key}"`)
        .join("\n");

      await sendMessage(
        page,
        `把以下文件上传到"幻夜之刃世界观"知识库。对每个文件执行 kb_upload_document，不要问我确认。

${uploadInstructions}

上传完告诉我结果。`
      );

      const uploadResp = await getLastResponse(page);
      log(`Step 2.5: Response (first 400 chars): ${uploadResp.slice(0, 400)}`);
      await screenshot(page, "02c-docs-uploaded");

      // Verify some docs were uploaded
      expect(uploadResp).toMatch(/上传|upload|成功|ingestion|文档|document/i);
      log("Step 2.5: DONE — documents uploaded to KB");

      // Grant bedrock:Retrieve permission to workspace role (required for KB queries)
      log("Step 2.5: Granting bedrock:Retrieve via MCP grant...");
      const grantResult = await page.evaluate(async () => {
        const keys = Object.keys(localStorage);
        const idTokenKey = keys.find(k => k.includes("idToken"));
        const token = idTokenKey ? localStorage.getItem(idTokenKey) || "" : "";
        const wsId = localStorage.getItem("agent-studio-workspace-id") || "";
        if (!token || !wsId) return "ERR:no_auth";

        const resp = await fetch(`${window.location.origin}/api/workspaces/${wsId}/grant-mcp`, {
          method: "POST",
          headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
          body: JSON.stringify({ targets: ["bedrock-kb-retrieval"] }),
        });
        if (!resp.ok) return `ERR:${resp.status}:${await resp.text().catch(() => "")}`;
        return "OK";
      });
      log(`Step 2.5: Grant result: ${typeof grantResult === 'string' ? grantResult.slice(0, 100) : grantResult}`);

      // Wait for ingestion to process
      log("Step 2.5: Waiting 30s for ingestion...");
      await page.waitForTimeout(30_000);
    });

    // ════════════════════════════════════════════════
    // STEP 3: Create Reviewer Agent via Meta-Agent
    // ════════════════════════════════════════════════
    await test.step("Create Worldview Reviewer Agent", async () => {
      log("Step 3: Creating Reviewer Agent...");

      await sendMessage(
        page,
        `帮我造一个世界观审核助手。

它的唯一职责是检查游戏内容有没有违反世界观设定。不靠记忆判断——每次审核前必须先查知识库，找到对应的设定原文再做判定。

审核要从这几个角度看：有没有泄露未公开的版本信息、角色说的话像不像这个人、有没有时间线矛盾、提到的地名是不是当前版本已公开的、角色关系有没有写错、语言风格是不是符合游戏调性。

判定分三级：红色（必须改）、黄色（建议改）、绿色（通过）。每条判定都要附上从知识库查到的依据。

当前游戏版本是 3.1，任何标注为 3.2 或 4.0 才公开的内容都算泄露。

agent_name 用 "worldviewReviewer"，绑定知识库"幻夜之刃世界观"。直接执行创建和部署，不要问我确认。`
      );

      const response = await getLastResponse(page);
      log(`Step 3: Response (first 300 chars): ${response.slice(0, 300)}`);
      await screenshot(page, "04-reviewer-created");

      expect(response).toMatch(/成功|created|deployed|ready|READY|创建完成|已创建/i);
      log("Step 3: DONE");
    });

    // ════════════════════════════════════════════════
    // STEP 4: Create Writer Agent via Meta-Agent
    // ════════════════════════════════════════════════
    await test.step("Create Dialog Writer Agent", async () => {
      log("Step 4: Creating Writer Agent...");

      await sendMessage(
        page,
        `帮我造一个 NPC 对白创作助手。

它要能根据我给的角色名和场景，写出符合角色性格的游戏对白。支持三种类型：idle（闲置）、剧情、战斗。

写之前先查一下知识库里这个角色的设定卡，了解说话风格和禁忌。

写完后自动交给世界观审核 Agent 检查，如果有不通过的就自动改，最多改 3 轮，还不过就把问题和当前版本一起告诉我让我决定。

整体风格要求：和风、克制、有诗意。idle 对白每条 15-40 字，剧情不超过 80 字，战斗不超过 15 字。

agent_name 用 "dialogWriter"，绑定知识库"幻夜之刃世界观"。直接执行创建和部署，不要问我确认。`
      );

      const response = await getLastResponse(page);
      log(`Step 4: Response (first 300 chars): ${response.slice(0, 300)}`);
      await screenshot(page, "05-writer-created");

      expect(response).toMatch(/成功|created|deployed|ready|READY|创建完成|已创建/i);
      log("Step 4: DONE");
    });

    // ════════════════════════════════════════════════
    // STEP 5: Link Writer → Reviewer
    // ════════════════════════════════════════════════
    await test.step("Link Writer → Reviewer via A2A", async () => {
      log("Step 5: Linking agents...");

      await sendMessage(
        page,
        `把 dialogWriter 和 worldviewReviewer 连起来。dialogWriter 写完对白后要自动调用 worldviewReviewer 来审核。执行 link_agent，source 是 dialogWriter，target 是 worldviewReviewer。不要问我确认。`
      );

      const response = await getLastResponse(page);
      log(`Step 5: Response (first 300 chars): ${response.slice(0, 300)}`);
      await screenshot(page, "06-linked");

      expect(response).toMatch(/成功|linked|连接|关联|已.*link|已关联|call_agent/i);
      log("Step 5: DONE");
    });

    // ════════════════════════════════════════════════
    // STEP 6: Chat with Writer Agent
    // ════════════════════════════════════════════════
    // ════════════════════════════════════════════════
    // STEP 5.5: Wait for agents to reach READY
    // ════════════════════════════════════════════════
    await test.step("Wait for agents to be READY", async () => {
      log("Step 5.5: Waiting for agents to finish deploying (120s)...");
      // Both agents need time after link_agent triggers a redeploy
      await page.waitForTimeout(120_000);

      // Confirm READY status via Meta-Agent
      await sendMessage(page, `查一下 dialogWriter 和 worldviewReviewer 的状态。只告诉我 agent_name 和 status。`);
      const statusResp = await getLastResponse(page);
      log(`Step 5.5: Status check: ${statusResp.slice(0, 200)}`);
      await screenshot(page, "06b-agents-ready");

      // If still not ready, wait more
      if (statusResp.includes("UPDATING") || statusResp.includes("CREATING")) {
        log("Step 5.5: Still updating, waiting 60s more...");
        await page.waitForTimeout(60_000);
      }
      log("Step 5.5: DONE");
    });

    // ════════════════════════════════════════════════
    // STEP 6: Chat with Writer Agent
    // ════════════════════════════════════════════════
    await test.step("Invoke Dialog Writer — generate dialog", async () => {
      log("Step 6: Switching to Writer Agent chat...");

      // Refresh sidebar to see new agents
      const refreshBtn = page.locator("button").filter({ has: page.locator("svg.lucide-refresh-cw") }).first();
      await refreshBtn.click().catch(() => {});
      await page.waitForTimeout(5_000);

      // Try to find Writer agent in sidebar and click it
      const writerLink = page.locator("text=dialogWriter").first();
      const writerVisible = await writerLink.isVisible().catch(() => false);

      if (writerVisible) {
        await writerLink.click();
        await page.waitForTimeout(3_000);
        log("Step 6: Clicked dialogWriter in sidebar");
      } else {
        // Fallback: ask Meta-Agent to invoke the writer on our behalf
        log("Step 6: Writer not in sidebar, asking Meta-Agent to relay...");
      }

      await screenshot(page, "07-writer-chat-ready");

      // Send the creative prompt via Meta-Agent call_agent
      log("Step 6: Sending creative prompt...");
      await sendMessage(
        page,
        `调用 dialogWriter，让它帮月見写 5 条 idle 对白。直接执行 call_agent，把结果完整返回给我。`,
        600_000
      );

      let response = await getLastResponse(page);
      await screenshot(page, "08-writer-response-attempt1");

      // If agent hit BOUND_KBS error and triggered redeploy, wait and retry
      if (response.includes("redeploy") || response.includes("重新部署") || response.includes("UPDATING") || response.includes("BOUND_KBS")) {
        log("Step 6: Agent triggered redeploy for KB fix — waiting 150s then retrying...");
        await page.waitForTimeout(150_000);

        await sendMessage(
          page,
          `dialogWriter 现在应该 READY 了。重新调用 call_agent，让它帮月見写 5 条 idle 对白。直接返回结果。`,
          600_000
        );
        response = await getLastResponse(page);
        await screenshot(page, "08-writer-response-retry");
      }

      await screenshot(page, "08-writer-response");
      log("Step 6: DONE — response received");
    });

    // ════════════════════════════════════════════════
    // STEP 7: Verify output
    // ════════════════════════════════════════════════
    await test.step("Verify dialog output", async () => {
      log("Step 7: Verifying output...");
      const response = await getLastResponse(page);
      log(`Step 7: Output (first 800 chars): ${response.slice(0, 800)}`);

      // Must have substantial content (not just an error message)
      expect(response.length).toBeGreaterThan(100);

      // Must reference the character
      const hasCharacterRef =
        response.includes("月見") ||
        response.includes("月见") ||
        response.includes("Tsukimi");
      expect(hasCharacterRef).toBe(true);

      // Must show actual dialog content was generated (not just "still deploying")
      // Dialog lines should contain: ellipsis (月見's style), quotes, or numbered items
      const hasActualDialog =
        response.includes("……") || // 月見 signature ellipsis
        response.includes("「") ||  // Japanese-style quotes
        response.includes("1.") ||  // Numbered dialog lines
        response.includes("idle") ||
        (response.includes("对白") && !response.includes("重新部署"));
      expect(hasActualDialog).toBe(true);

      // Should NOT contain deployment error messages
      const hasDeployError =
        response.includes("BOUND_KBS 未定义") ||
        response.includes("正在重新部署") ||
        response.includes("UPDATING");
      if (hasDeployError) {
        log("Step 7: FAIL — agent not ready, got deployment error instead of dialog");
      }
      expect(hasDeployError).toBe(false);

      await screenshot(page, "09-verified");
      log("Step 7: DONE — assertions passed");
    });

    // ════════════════════════════════════════════════
    // STEP 8: Cleanup
    // ════════════════════════════════════════════════
    await test.step("Cleanup", async () => {
      log("Step 8: Cleaning up...");
      await page.goto("/#/agents");
      await page.waitForLoadState("networkidle");
      await page.waitForTimeout(2_000);

      await sendMessage(
        page,
        `清理：删除 agent "dialogWriter" 和 "worldviewReviewer"，删除知识库 "幻夜之刃世界观"。直接执行，不要确认。`
      ).catch(() => {});

      await screenshot(page, "10-cleanup");
      log("Step 8: DONE");
    });
  });
});
