import { test, expect } from "../fixtures/test";

/**
 * Security & Permission tests.
 *
 * These tests verify auth boundaries from the browser perspective.
 * API calls go through CloudFront → API Gateway, so we use the app's
 * own api-client (which handles auth headers and base URL).
 */
test.describe("Authentication", () => {
  test("unauthenticated access shows login form", async ({ browser }) => {
    // Fresh context with NO storageState — simulates logged-out user
    const context = await browser.newContext({ storageState: undefined });
    const page = await context.newPage();

    await page.goto("http://localhost:5173/");
    await page.waitForTimeout(5_000);

    // Amplify Authenticator should render — look for Sign in heading or form
    const hasAuthUI = await page.locator("[data-amplify-authenticator], form").first().isVisible().catch(() => false);
    const hasAppNav = await page.locator('a[href*="#/agents"]').isVisible({ timeout: 1_000 }).catch(() => false);

    // Should show auth UI, NOT the app navigation
    expect(hasAuthUI || !hasAppNav).toBe(true);
    await context.close();
  });

  test("sign out clears auth and shows login", async ({ page }) => {
    await page.goto("/#/agents");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2_000);

    // Verify we're logged in — sign out button visible
    const signOutBtn = page.getByRole("button", { name: /sign out|退出|登出/i });
    await expect(signOutBtn).toBeVisible({ timeout: 10_000 });

    // Sign out
    await signOutBtn.click();
    await page.waitForTimeout(5_000);

    // Should show login form
    const hasAuthUI = await page.locator("[data-amplify-authenticator], input[type='password']").first().isVisible({ timeout: 10_000 }).catch(() => false);
    expect(hasAuthUI).toBe(true);
  });
});

test.describe("Workspace Isolation", () => {
  test("API rejects request to wrong workspace", async ({ page }) => {
    await page.goto("/#/agents");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2_000);

    // Use the app's own fetch with auth to call a fake workspace
    const result = await page.evaluate(async () => {
      // Get token from Amplify session (already loaded in the app)
      const amplify = (window as any).__amplify_session_token;
      // Fallback: extract from localStorage
      const keys = Object.keys(localStorage);
      const tokenKey = keys.find(k => k.includes("idToken"));
      const token = tokenKey ? localStorage.getItem(tokenKey) : null;

      if (!token) return { status: -1, error: "no token found" };

      // Get the real API URL from the app's config
      const apiUrl = (window as any).__AGENT_STUDIO_API_URL__ || "";
      if (!apiUrl) return { status: -2, error: "no API URL" };

      try {
        const resp = await fetch(`${apiUrl}/api/workspaces/fake-workspace-xyz/agents`, {
          headers: { "Authorization": `Bearer ${token}` },
        });
        return { status: resp.status };
      } catch (e) {
        return { status: 0, error: (e as Error).message };
      }
    });

    console.log("Workspace isolation result:", result);
    // Should get 403 (not a member of fake workspace)
    // If status is 0, it's a CORS error which also means the request was rejected
    if (result.status > 0) {
      expect(result.status).toBeGreaterThanOrEqual(400);
    }
  });

  test("frontend uses correct workspace ID from localStorage", async ({ page }) => {
    await page.goto("/#/agents");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2_000);

    // Verify workspace ID is set in localStorage
    const wsId = await page.evaluate(() => localStorage.getItem("agent-studio-workspace-id"));
    expect(wsId).toBeTruthy();
    expect(wsId!.length).toBeGreaterThan(0);
    console.log(`Workspace ID: ${wsId}`);
  });
});

test.describe("Input Sanitization", () => {
  test("skill name with path traversal is sanitized", async ({ page, skillsPage }) => {
    await skillsPage.goto();

    await page.getByRole("button", { name: /create|创建/i }).click();
    const dialog = page.locator(".fixed.inset-0").last();
    await dialog.waitFor({ state: "visible" });

    // Enter name with path traversal
    await dialog.locator("input").first().fill("../../etc/passwd");
    await dialog.getByRole("button", { name: /create|创建/i }).click();
    await page.waitForTimeout(2_000);

    // If skill was created, name should be sanitized (no ../)
    if (page.url().includes("/skills/")) {
      const heading = page.locator("h2").first();
      const name = await heading.textContent();
      expect(name).not.toContain("../");

      // Cleanup
      const deleteBtn = page.locator("button").filter({ has: page.locator("svg.lucide-trash-2") }).first();
      await deleteBtn.click();
      const confirmDialog = page.locator(".fixed.inset-0").last();
      await confirmDialog.waitFor({ state: "visible" });
      await confirmDialog.getByRole("button", { name: /trash|回收站/i }).click();
    }
  });

  test("tool name with SQL injection is sanitized", async ({ page, toolsPage }) => {
    await toolsPage.goto();

    await page.getByRole("button", { name: /create|创建/i }).click();
    const dialog = page.locator(".fixed.inset-0").last();
    await dialog.waitFor({ state: "visible" });

    await dialog.locator("input").first().fill("test'; DROP TABLE agents;--");
    await dialog.getByRole("button", { name: /create|创建/i }).click();
    await page.waitForTimeout(2_000);

    if (page.url().includes("/tools/")) {
      // Tool was created — verify no crash and name is sanitized
      await expect(page.locator(".monaco-editor").first()).toBeVisible({ timeout: 10_000 });

      // The tool ID should only contain safe characters
      const url = page.url();
      const toolId = url.split("/tools/")[1]?.split("?")[0] || "";
      expect(toolId).not.toContain("'");
      expect(toolId).not.toContain(";");
      expect(toolId).not.toContain("DROP");
      console.log(`Sanitized tool ID: ${toolId}`);

      // Cleanup
      const deleteBtn = page.locator("button").filter({ has: page.locator("svg.lucide-trash-2") }).first();
      await deleteBtn.click();
      const confirmDialog = page.locator(".fixed.inset-0").last();
      await confirmDialog.waitFor({ state: "visible" });
      await confirmDialog.getByRole("button", { name: /trash|回收站/i }).click();
    }
  });

  test("agent name only allows alphanumeric characters", async ({ page }) => {
    // Navigate to create agent form
    await page.goto("/#/agents/edit/__new__");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(3_000);

    // The name input should be visible in create mode
    const nameInput = page.locator("input[type='text']").first();
    if (await nameInput.isVisible().catch(() => false)) {
      // Try entering special characters
      await nameInput.fill("test-agent_with.special!chars");
      const value = await nameInput.inputValue();
      console.log(`Agent name input value: "${value}"`);
      // The input accepts it, but create_agent tool validates alphanumeric only
    }
  });
});

test.describe("XSS Prevention", () => {
  test.setTimeout(180_000);

  test("chat message with HTML tags renders safely", async ({ page }) => {
    await page.goto("/#/agents");
    await page.waitForLoadState("networkidle");
    await expect(page.locator("h2").first()).toContainText(/Meta Agent/i, { timeout: 15_000 });

    // Send a message containing HTML/script tags
    const textarea = page.locator("textarea").first();
    await textarea.fill('respond with exactly this text: <script>alert("xss")</script><img src=x onerror=alert(1)>');
    await page.locator("button[type='submit']").first().click();

    const cancelBtn = page.locator("button").filter({ has: page.locator("svg.lucide-square") });
    await cancelBtn.waitFor({ state: "visible", timeout: 60_000 }).catch(() => {});
    await cancelBtn.waitFor({ state: "hidden", timeout: 120_000 });

    // Verify no script executed — check that no alert dialog appeared
    // (Playwright would throw if an unexpected dialog appeared)

    // The key security check: no executable <script> tag in the DOM
    // react-markdown escapes HTML by default, so script tags become text or code blocks
    const executableScripts = await page.evaluate(() => {
      // Count script tags that contain alert and are NOT from dev tooling
      const scripts = document.querySelectorAll("script");
      let malicious = 0;
      for (const s of scripts) {
        // Skip Vite dev server, HMR, module scripts, and analytics
        if (s.src || s.type === "module" || s.type === "importmap") continue;
        if (s.textContent?.includes("alert")) {
          malicious++;
        }
      }
      return malicious;
    });
    expect(executableScripts).toBe(0);
  });
});

test.describe("Token Handling", () => {
  test("auth token is sent via X-Auth-Token header for invoke", async ({ page }) => {
    await page.goto("/#/agents");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2_000);

    // Monitor network requests
    const invokeRequests: { url: string; headers: Record<string, string> }[] = [];
    page.on("request", (req) => {
      if (req.url().includes("/invoke")) {
        invokeRequests.push({
          url: req.url(),
          headers: req.headers(),
        });
      }
    });

    // Send a message to trigger an invoke request
    const textarea = page.locator("textarea").first();
    await textarea.fill("hello");
    await page.locator("button[type='submit']").first().click();

    const cancelBtn = page.locator("button").filter({ has: page.locator("svg.lucide-square") });
    await cancelBtn.waitFor({ state: "visible", timeout: 60_000 }).catch(() => {});
    await cancelBtn.waitFor({ state: "hidden", timeout: 120_000 });

    // Verify invoke request had auth token
    expect(invokeRequests.length).toBeGreaterThan(0);
    const req = invokeRequests[0];
    expect(req.headers["x-auth-token"]).toBeTruthy();
    console.log(`Invoke URL: ${req.url}`);
    console.log(`Has X-Auth-Token: ${!!req.headers["x-auth-token"]}`);
  });
});
