import { test, expect } from "../fixtures/test";
import { testName } from "../helpers/utils";

/**
 * Full closed-loop test: Create Agent → Deploy → Chat → Verify → Cleanup
 *
 * This test assumes the system may have bugs and logs detailed diagnostics.
 * If it fails, the logs and screenshots reveal exactly where the pipeline breaks.
 */
test.describe("Agent Create → Deploy → Chat — Full Closed Loop", () => {
  test.setTimeout(600_000);

  const AGENT_NAME = testName("fullloop").replace(/-/g, "");
  const MARKER = `PONG_${Date.now().toString(36).toUpperCase()}`;

  /**
   * Full closed-loop: Create Agent via Meta-Agent → verify in sidebar → chat → verify response → cleanup.
   *
   * Previously blocked by a bug where create_agent didn't write workspace_id to DynamoDB.
   * Fixed in: lambda/invoke-node/handler.mjs, meta-agent/main.py, meta-agent/tools/create_agent.py
   */
  test("create agent via Meta-Agent → deploy → chat → verify exact response → cleanup", async ({ page }) => {
    // ══════════════════════════════════════════════
    // STEP 1: Ask Meta-Agent to create and deploy the agent
    // ══════════════════════════════════════════════
    await page.goto("/#/agents");
    await page.waitForLoadState("networkidle");
    await expect(page.locator("h2").first()).toContainText(/Meta Agent/i, { timeout: 15_000 });

    const textarea = page.locator("textarea").first();
    await textarea.fill(
      `Create and deploy an agent with these EXACT parameters:
- agent_name: "${AGENT_NAME}"
- description: "E2E test bot"
- system_prompt: "You are a test bot. When the user says ping, respond with exactly: ${MARKER}"
- template_id: "general"
- No tools, no skills needed.

Execute create_agent immediately. Do NOT ask for confirmation. After creation, wait for it to be READY.`
    );
    await page.locator("button[type='submit']").first().click();

    // Wait for Meta-Agent to finish
    const cancelBtn = page.locator("button").filter({ has: page.locator("svg.lucide-square") });
    await cancelBtn.waitFor({ state: "visible", timeout: 60_000 }).catch(() => {});
    await cancelBtn.waitFor({ state: "hidden", timeout: 480_000 });

    // Log the full Meta-Agent response for diagnostics
    const allResponses = await page.locator(".prose, .react-markdown").allTextContents();
    const fullResponse = allResponses.join("\n---\n");
    console.log("=== META-AGENT FULL RESPONSE ===");
    console.log(fullResponse.slice(0, 2000));
    console.log("================================");

    // ASSERTION 1: Meta-Agent should mention success
    const hasSuccess = /success|成功|created|ready|READY|agent_id/i.test(fullResponse);
    expect(hasSuccess).toBe(true);

    // ══════════════════════════════════════════════
    // STEP 2: Poll sidebar for the new agent
    // ══════════════════════════════════════════════
    const sidebar = page.locator("aside").first();
    const refreshBtn = page.locator("button").filter({ has: page.locator("svg.lucide-refresh-cw") }).first();

    let agentFound = false;
    let sidebarAgents: string[] = [];
    for (let poll = 0; poll < 6; poll++) { // 6 * 5s = 30s — quick check, we know it's likely a bug
      await refreshBtn.click();
      await page.waitForTimeout(5_000);

      sidebarAgents = await sidebar.locator("span.text-sm.font-medium").allTextContents();
      console.log(`Poll ${poll + 1}/6: sidebar = [${sidebarAgents.join(", ")}]`);

      if (sidebarAgents.some(n => n.toLowerCase().includes(AGENT_NAME.toLowerCase()))) {
        agentFound = true;
        break;
      }
    }

    // ASSERTION 2: Agent should appear in sidebar
    // If this fails, it's a bug — agent was created but never shows up in the list
    expect.soft(agentFound, `Agent "${AGENT_NAME}" not found in sidebar after 4 min. Sidebar contains: [${sidebarAgents.join(", ")}]`).toBe(true);

    if (!agentFound) {
      // BUG DETECTED: Agent created in AgentCore but not in DynamoDB/CRUD API
      console.log("BUG: Agent created successfully (READY) but never appeared in sidebar list.");
      console.log("This suggests create_agent doesn't register the agent in the CRUD API/DynamoDB.");

      // Try to extract the real agent_id from the tool output JSON
      const jsonMatch = fullResponse.match(/"agent_id":\s*"([^"]+)"/);
      if (jsonMatch) {
        const realAgentId = jsonMatch[1];
        console.log(`Extracted real agent_id: ${realAgentId}`);
        await page.goto(`/#/agents/chat/${realAgentId}`);
        await page.waitForTimeout(5_000);
      } else {
        console.log("Could not extract agent_id from JSON output.");
        await cleanupAgent(page, AGENT_NAME);
        expect.soft(agentFound, "BUG: Agent created (READY) but not in sidebar. create_agent may not write to DynamoDB.").toBe(true);
        return;
      }
    } else {
      // Click the agent in sidebar
      const agentCard = sidebar.locator(".group").filter({ hasText: new RegExp(AGENT_NAME, "i") }).first();
      await agentCard.locator("button").first().click();
      await page.waitForTimeout(3_000);
    }

    // ASSERTION 3: Should be on agent chat page
    const chatUrl = page.url();
    console.log("Chat URL:", chatUrl);
    expect.soft(chatUrl).toMatch(/#\/agents\/chat\//);

    // ══════════════════════════════════════════════
    // STEP 3: Send "ping" and verify the EXACT marker response
    // ══════════════════════════════════════════════
    const chatTextarea = page.locator("textarea").first();
    await expect(chatTextarea).toBeVisible({ timeout: 10_000 });

    let gotMarker = false;
    let lastResponse = "";
    for (let attempt = 0; attempt < 5; attempt++) { // 5 attempts with longer waits
      const newSessionBtn = page.locator("button").filter({ has: page.locator("svg.lucide-plus") }).last();
      await newSessionBtn.click();
      await page.waitForTimeout(1_000);

      await chatTextarea.fill("ping");
      await page.locator("button[type='submit']").first().click();

      const chatCancel = page.locator("button").filter({ has: page.locator("svg.lucide-square") });
      await chatCancel.waitFor({ state: "visible", timeout: 60_000 }).catch(() => {});
      await chatCancel.waitFor({ state: "hidden", timeout: 120_000 });

      lastResponse = await page.locator(".prose, .react-markdown").last().textContent() || "";
      console.log(`Chat attempt ${attempt + 1}/5: "${lastResponse.slice(0, 200)}"`);

      if (lastResponse.includes(MARKER)) {
        gotMarker = true;
        break;
      }

      // If we get 424 or error, agent is still deploying — wait longer
      if (/error|424|initializing/i.test(lastResponse)) {
        console.log("Agent still deploying, waiting 30s...");
        await page.waitForTimeout(30_000);
      } else {
        await page.waitForTimeout(10_000);
      }
    }

    // ASSERTION 4: Agent should respond with our exact marker
    expect(gotMarker, `BUG: Agent did not respond with marker "${MARKER}". Last response: "${lastResponse.slice(0, 300)}"`).toBe(true);

    // ══════════════════════════════════════════════
    // STEP 4: Cleanup
    // ══════════════════════════════════════════════
    await cleanupAgent(page, AGENT_NAME);
  });
});

/** Helper: delete agent via Meta-Agent */
async function cleanupAgent(page: import("@playwright/test").Page, agentName: string) {
  await page.goto("/#/agents");
  await page.waitForTimeout(2_000);

  const newSessionBtn = page.locator("button").filter({ has: page.locator("svg.lucide-plus") }).last();
  await newSessionBtn.click();
  await page.waitForTimeout(1_000);

  const textarea = page.locator("textarea").first();
  await textarea.fill(`Delete agent with agent_id: ${agentName}. Execute delete_agent immediately. Do NOT ask for confirmation.`);
  await page.locator("button[type='submit']").first().click();

  const cancelBtn = page.locator("button").filter({ has: page.locator("svg.lucide-square") });
  await cancelBtn.waitFor({ state: "visible", timeout: 30_000 }).catch(() => {});
  await cancelBtn.waitFor({ state: "hidden", timeout: 60_000 });
}
