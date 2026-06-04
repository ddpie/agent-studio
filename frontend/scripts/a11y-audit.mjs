/**
 * Accessibility Audit Script
 *
 * Builds and serves Storybook, then visits each story iframe with Playwright
 * and runs axe-core analysis. Reports violations grouped by severity.
 *
 * Usage:
 *   node scripts/a11y-audit.mjs [--serve-only] [--max-stories N]
 *
 * Prerequisites:
 *   - storybook-static/ must exist (run `npx storybook build` first)
 *   - @axe-core/playwright must be installed
 */

import { chromium } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { createServer } from "http";
import { readFileSync, existsSync } from "fs";
import { join, extname } from "path";
import { fileURLToPath } from "url";

const __dirname = fileURLToPath(new URL(".", import.meta.url));
const STORYBOOK_DIR = join(__dirname, "..", "storybook-static");
const PORT = 6099;

// MIME types for serving static files
const MIME_TYPES = {
  ".html": "text/html",
  ".js": "application/javascript",
  ".css": "text/css",
  ".json": "application/json",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".woff2": "font/woff2",
};

function serveStatic(dir) {
  return createServer((req, res) => {
    let filePath = join(dir, req.url === "/" ? "/index.html" : req.url);
    // Strip query params
    filePath = filePath.split("?")[0];

    if (!existsSync(filePath)) {
      res.writeHead(404);
      res.end("Not found");
      return;
    }

    const ext = extname(filePath);
    const mime = MIME_TYPES[ext] || "application/octet-stream";
    const content = readFileSync(filePath);
    res.writeHead(200, { "Content-Type": mime });
    res.end(content);
  });
}

async function getStoryIds() {
  const indexPath = join(STORYBOOK_DIR, "index.json");
  const data = JSON.parse(readFileSync(indexPath, "utf-8"));
  const entries = data.entries || data.v || {};
  // Only include "story" type entries (not docs)
  return Object.entries(entries)
    .filter(([, v]) => v.type === "story")
    .map(([id]) => id);
}

async function runAudit(maxStories) {
  if (!existsSync(STORYBOOK_DIR)) {
    console.error(
      "ERROR: storybook-static/ not found. Run `npx storybook build` first."
    );
    process.exit(1);
  }

  // Start static server
  const server = serveStatic(STORYBOOK_DIR);
  await new Promise((resolve) => server.listen(PORT, resolve));
  console.log(`Serving storybook on http://localhost:${PORT}`);

  const storyIds = await getStoryIds();
  const total = maxStories ? Math.min(storyIds.length, maxStories) : storyIds.length;
  console.log(`Found ${storyIds.length} stories, auditing ${total}...\n`);

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();

  const allViolations = [];
  let storiesWithViolations = 0;
  let storiesClean = 0;
  let storiesErrored = 0;

  for (let i = 0; i < total; i++) {
    const storyId = storyIds[i];
    const url = `http://localhost:${PORT}/iframe.html?id=${storyId}&viewMode=story`;
    const page = await context.newPage();

    try {
      await page.goto(url, { waitUntil: "networkidle", timeout: 15000 });
      // Wait a bit for any animations/rendering
      await page.waitForTimeout(500);

      const results = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"])
        .analyze();

      if (results.violations.length > 0) {
        storiesWithViolations++;
        for (const v of results.violations) {
          allViolations.push({
            storyId,
            id: v.id,
            impact: v.impact,
            description: v.description,
            help: v.help,
            helpUrl: v.helpUrl,
            nodes: v.nodes.length,
            targets: v.nodes.slice(0, 3).map((n) => n.target.join(" ")),
          });
        }
      } else {
        storiesClean++;
      }

      // Progress indicator
      if ((i + 1) % 10 === 0 || i === total - 1) {
        process.stdout.write(`  [${i + 1}/${total}] stories audited\r`);
      }
    } catch (err) {
      storiesErrored++;
      console.error(`  ERROR on ${storyId}: ${err.message.slice(0, 100)}`);
    } finally {
      await page.close();
    }
  }

  await browser.close();
  server.close();

  // Aggregate and report
  console.log("\n\n" + "=".repeat(80));
  console.log("ACCESSIBILITY AUDIT REPORT");
  console.log("=".repeat(80));
  console.log(`\nStories audited: ${total}`);
  console.log(`  Clean (no violations): ${storiesClean}`);
  console.log(`  With violations: ${storiesWithViolations}`);
  console.log(`  Errored: ${storiesErrored}`);
  console.log(`\nTotal violations found: ${allViolations.length}`);

  // Group by severity
  const bySeverity = { critical: [], serious: [], moderate: [], minor: [] };
  for (const v of allViolations) {
    const bucket = bySeverity[v.impact] || bySeverity.moderate;
    bucket.push(v);
  }

  for (const severity of ["critical", "serious", "moderate", "minor"]) {
    const items = bySeverity[severity];
    if (items.length === 0) continue;

    console.log(`\n${"─".repeat(80)}`);
    console.log(
      `${severity.toUpperCase()} (${items.length} violation${items.length > 1 ? "s" : ""})`
    );
    console.log("─".repeat(80));

    // Group by rule
    const byRule = {};
    for (const item of items) {
      if (!byRule[item.id]) {
        byRule[item.id] = { ...item, stories: [item.storyId], totalNodes: item.nodes };
      } else {
        byRule[item.id].stories.push(item.storyId);
        byRule[item.id].totalNodes += item.nodes;
      }
    }

    for (const [ruleId, rule] of Object.entries(byRule)) {
      console.log(`\n  [${ruleId}] ${rule.help}`);
      console.log(`    ${rule.description}`);
      console.log(`    Affected stories: ${rule.stories.length} | Total nodes: ${rule.totalNodes}`);
      console.log(`    Example targets: ${rule.targets.join(", ")}`);
      console.log(`    More info: ${rule.helpUrl}`);
      if (rule.stories.length <= 5) {
        console.log(`    Stories: ${rule.stories.join(", ")}`);
      } else {
        console.log(
          `    Stories (first 5): ${rule.stories.slice(0, 5).join(", ")} ...and ${rule.stories.length - 5} more`
        );
      }
    }
  }

  console.log("\n" + "=".repeat(80));
  console.log("END OF REPORT");
  console.log("=".repeat(80));

  // Exit with non-zero if critical/serious violations found
  if (bySeverity.critical.length > 0 || bySeverity.serious.length > 0) {
    process.exit(1);
  }
}

// Parse args
const args = process.argv.slice(2);
const maxStoriesIdx = args.indexOf("--max-stories");
const maxStories = maxStoriesIdx >= 0 ? parseInt(args[maxStoriesIdx + 1], 10) : null;

runAudit(maxStories).catch((err) => {
  console.error("Fatal error:", err);
  process.exit(2);
});
