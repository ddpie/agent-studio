import { test, expect } from "../fixtures/test";

/**
 * F3 probe — verifies the chat-store persist-side image sanitization.
 *
 * Before the fix, chat-store persisted raw `data:image/...` data URLs into
 * localStorage, which silently dropped messages once localStorage hit its
 * 5-10 MB browser cap. The fix adds `_stripDataUrlImages` and caps to the
 * persist `partialize` path, and ChatInput.tsx refuses to send until every
 * image has a stable S3 URL.
 *
 * This probe verifies the INVARIANT, not the forward path:
 *   "localStorage for key `agent-studio-chat` must never contain a
 *    `data:image` substring, even if seeded with legacy data-URL messages."
 *
 * Strategy:
 * - Seed localStorage with a message whose images array includes a tiny
 *   1x1 PNG data URL (simulates legacy state or a future bug leaking one).
 * - Trigger a Zustand persist flush by calling the store's setState
 *   through the UI (click refresh, send message, etc.) or simply reloading
 *   the page — reload causes Zustand to rehydrate, then the next write
 *   re-runs partialize and strips data URLs.
 * - Read localStorage back; assert the data URL is gone.
 *
 * If F3 regresses (e.g. someone removes `_stripDataUrlImages`), this probe
 * fails because the seeded data URL survives persistence.
 */

const TINY_PNG_DATA_URL =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=";

const STORE_KEY = "agent-studio-chat";

test.describe("F3 probe: data URLs are stripped from persisted chat state", () => {
  test.setTimeout(60_000);

  test("legacy data:image URL in localStorage is stripped after persist rehydrate", async ({ page }) => {
    // Load app once to establish origin + let Zustand initialize.
    await page.goto("/#/agents");
    await page.waitForLoadState("networkidle");

    // Seed localStorage with a message that contains a data-URL image.
    // We merge into whatever Zustand already persisted (preserves other fields).
    await page.evaluate(
      ({ key, pngUrl }) => {
        const raw = localStorage.getItem(key);
        const parsed = raw ? JSON.parse(raw) : { state: {}, version: 0 };
        const seededMsg = {
          id: "probe-seed-" + Date.now(),
          role: "user",
          content: "probe seed",
          images: [pngUrl, "https://s3.us-east-1.amazonaws.com/bucket/real.png"],
          timestamp: Date.now(),
        };
        parsed.state = parsed.state || {};
        parsed.state.messages = [seededMsg];
        localStorage.setItem(key, JSON.stringify(parsed));
      },
      { key: STORE_KEY, pngUrl: TINY_PNG_DATA_URL },
    );

    // Sanity check: data URL really is in localStorage before the test.
    const beforeReload = await page.evaluate((key) => localStorage.getItem(key), STORE_KEY);
    expect(beforeReload, "seed step failed").toContain("data:image");

    // Reload — Zustand rehydrates from localStorage, then any subsequent
    // write triggers partialize. Navigate to a different route and back to
    // force a state write (switching agents updates `currentAgentId`).
    await page.reload();
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(500);

    // Trigger a persist write by navigating to agents page (sidebar click
    // updates currentAgentId state → partialize runs → sanitize runs).
    const firstAgentCard = page.locator(".group.w-full").first();
    if (await firstAgentCard.isVisible({ timeout: 5_000 }).catch(() => false)) {
      await firstAgentCard.click();
      await page.waitForTimeout(500);
    }

    // HARD ASSERTION: no data:image substring survives persistence.
    const afterReload = await page.evaluate((key) => localStorage.getItem(key), STORE_KEY);
    expect(afterReload, "localStorage should have agent-studio-chat entry").toBeTruthy();
    expect(
      afterReload!.includes("data:image"),
      `F3 BROKEN: data:image URL survived persistence. First 200 chars: ${afterReload!.slice(0, 200)}`,
    ).toBe(false);

    // Positive check: the S3 URL (valid) should still be there.
    // (Only if our seeded message itself survived the persist trim.)
    // We don't hard-require this — the trim may drop old messages beyond
    // MAX_ACTIVE_MESSAGES — but if the seeded message did survive, its
    // non-data-URL image should be preserved.
    if (afterReload!.includes("probe-seed-")) {
      expect(
        afterReload!.includes("s3.us-east-1.amazonaws.com/bucket/real.png"),
        "F3 regression: sanitize stripped legitimate S3 URL",
      ).toBe(true);
    }
  });
});
