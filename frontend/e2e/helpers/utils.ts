import type { Page } from "@playwright/test";

/**
 * Wait for the app shell to be ready (post-auth, post-workspace-load).
 * Assumes storageState is already applied.
 */
export async function waitForAppReady(page: Page) {
  // The hash router renders IconNav with navigation links
  await page.waitForSelector('a[href*="#/agents"], a[href*="#/skills"]', {
    state: "visible",
    timeout: 30_000,
  });
}

/**
 * Generate a unique test name with prefix + timestamp to avoid collisions.
 */
export function testName(prefix: string): string {
  const ts = Date.now().toString(36);
  return `e2e-${prefix}-${ts}`;
}
