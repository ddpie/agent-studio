import { test, expect } from "../fixtures/test";
import { execSync } from "child_process";

/**
 * F1 probe — verifies the delete_agent tool field-name fix.
 *
 * Before the fix, Meta-Agent's delete_agent tool read `record.get("owner")`
 * but DynamoDB stores the field as `created_by`. Every archive attempt
 * returned "Permission denied: agent owned by None", silently.
 *
 * This probe bypasses soft UI assertions and verifies the persisted DDB
 * state directly: after archive, status must flip from "active" to "archived".
 * If F1 regresses, DDB never changes and this test fails loudly.
 *
 * Strategy:
 * - Intercept the /agents list API response to learn the active agent's
 *   DDB id and display name.
 * - Locate the sidebar card by displayName text, click its archive button.
 * - Query DDB via aws-cli after Meta-Agent streaming completes.
 *
 * Scope note: restore is intentionally NOT tested here — it relies on
 * list_agents returning archived agents, which is tracked separately as F6.
 * The probe leaves the test agent in archived state; restore manually or
 * via a future probe once F6 is fixed.
 */

const AWS_REGION = "us-east-1";
const AGENTS_TABLE = "agent-studio-agents";

/**
 * Read an agent's effective status from DynamoDB.
 *
 * Returns:
 *   - "archived" if status field exists and equals "archived"
 *   - "active" if status field is missing OR equals "active" (matches CRUD
 *     list_agents filter: `attribute_not_exists(#st) OR #st <> :archived`)
 *   - null if the agent doesn't exist in DDB at all
 */
function readAgentStatus(agentId: string): string | null {
  const out = execSync(
    `aws dynamodb get-item --table-name ${AGENTS_TABLE} ` +
    `--key '${JSON.stringify({ agentId: { S: agentId } })}' ` +
    `--projection-expression "agentId, #s" ` +
    `--expression-attribute-names '{"#s":"status"}' ` +
    `--region ${AWS_REGION} --output json`,
    { encoding: "utf8" },
  );
  const parsed = JSON.parse(out || "{}");
  const item = parsed.Item;
  if (!item || !item.agentId) return null; // Agent doesn't exist
  return item.status?.S ?? "active"; // Missing status = active
}

interface ListAgentsItem {
  agentId: string;
  name?: string;
  display_name?: string;
  status?: string;
}

test.describe("F1 probe: archive flips DDB status (hard assertion)", () => {
  test.setTimeout(300_000);

  test("archive flips DDB status from active to archived", async ({ page }) => {
    // Capture the /agents list response so we can map sidebar cards to DDB ids.
    let firstActive: ListAgentsItem | undefined;
    page.on("response", async (resp) => {
      if (firstActive) return;
      const url = resp.url();
      if (!url.includes("/agents") || resp.request().method() !== "GET") return;
      if (!/workspaces\/[^/]+\/agents(\?|$)/.test(url)) return;
      try {
        const body = await resp.json();
        const items: ListAgentsItem[] = body.items || [];
        firstActive = items.find((it) => (it.status ?? "active") === "active");
      } catch { /* ignore non-JSON */ }
    });

    await page.goto("/#/agents");
    await page.waitForLoadState("networkidle");
    // Give the interceptor a chance to see the response.
    await page.waitForTimeout(2_000);
    expect(firstActive, "No active agent found for probe").toBeTruthy();
    const target = firstActive!;
    const agentId = target.agentId;
    const displayName = target.display_name || target.name || agentId;
    console.log(`[probe] target agentId=${agentId} displayName=${displayName}`);
    test.info().annotations.push({ type: "probe-target", description: `${agentId} (${displayName})` });

    // Pre-condition — agent must actually be active in DDB right now.
    const preStatus = readAgentStatus(agentId);
    console.log(`[probe] DDB pre-status for ${agentId}: ${preStatus}`);
    expect(preStatus).toBe("active");

    // Find the sidebar card by displayName. Displays are truncated with
    // ellipsis, so match by partial text.
    const cardTextLocator = page.locator('.group.w-full').filter({
      hasText: displayName.slice(0, 15),
    }).first();
    await expect(cardTextLocator).toBeVisible({ timeout: 10_000 });

    // --- Archive ---
    const archiveBtn = cardTextLocator.locator("button").filter({ has: page.locator("svg.lucide-archive") });
    await archiveBtn.click();

    const confirmDialog = page.locator(".fixed.inset-0").last();
    await expect(confirmDialog).toBeVisible({ timeout: 5_000 });
    await confirmDialog.getByRole("button", { name: /archive|归档/i }).click();

    // Wait for Meta-Agent streaming to finish.
    const cancelBtn = page.locator("button").filter({ has: page.locator("svg.lucide-square") });
    await cancelBtn.waitFor({ state: "visible", timeout: 30_000 }).catch(() => {});
    await cancelBtn.waitFor({ state: "hidden", timeout: 120_000 });

    // HARD ASSERTION: DDB status must now be "archived".
    const afterArchive = readAgentStatus(agentId);
    expect(
      afterArchive,
      `F1 BROKEN: archive did not flip DDB status. Agent ${agentId} still status=${afterArchive}`,
    ).toBe("archived");
  });
});
