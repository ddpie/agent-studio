import { describe, it, expect } from "vitest";
import {
  buildExpression,
  parseExpression,
  nextOccurrences,
} from "../cron-builder";

describe("cron-builder: build", () => {
  it("minutes → rate()", () => {
    expect(buildExpression({ mode: "minutes", rateValue: 15, rateUnit: "minutes" })).toBe("rate(15 minutes)");
    expect(buildExpression({ mode: "minutes", rateValue: 2, rateUnit: "hours" })).toBe("rate(2 hours)");
  });

  it("hourly: minute only", () => {
    expect(buildExpression({ mode: "hourly", minute: 30 })).toBe("cron(30 * * * ? *)");
  });

  it("daily: H:M UTC", () => {
    expect(buildExpression({ mode: "daily", minute: 0, hour: 9 })).toBe("cron(0 9 * * ? *)");
  });

  it("weekly: dow list is 1-based in cron (Sun=1)", () => {
    // UI day 1 (Mon) + 3 (Wed) → cron 2,4
    expect(buildExpression({ mode: "weekly", minute: 0, hour: 9, weekdays: [1, 3] })).toBe(
      "cron(0 9 ? * 2,4 *)",
    );
  });

  it("monthly: dom 15 at 23:45", () => {
    expect(buildExpression({ mode: "monthly", minute: 45, hour: 23, dayOfMonth: 15 })).toBe(
      "cron(45 23 15 * ? *)",
    );
  });

  it("advanced: passes raw through", () => {
    expect(buildExpression({ mode: "advanced", raw: "rate(7 days)" })).toBe("rate(7 days)");
  });

  it("clamps out-of-range values", () => {
    expect(buildExpression({ mode: "daily", minute: 99, hour: 99 })).toBe("cron(59 23 * * ? *)");
  });
});

describe("cron-builder: parse round-trip", () => {
  const cases: Array<[string, string]> = [
    ["minutes", "rate(5 minutes)"],
    ["hourly", "cron(30 * * * ? *)"],
    ["daily", "cron(0 9 * * ? *)"],
    ["weekly", "cron(0 9 ? * 2,4 *)"],
    ["monthly", "cron(45 23 15 * ? *)"],
  ];
  for (const [name, expr] of cases) {
    it(`parse+build round-trip (${name})`, () => {
      const spec = parseExpression(expr);
      expect(buildExpression(spec)).toBe(expr);
    });
  }

  it("falls back to advanced for expressions outside preset vocabulary", () => {
    // Range in dow field — not a preset we build.
    expect(parseExpression("cron(0 9 ? * 2-6 *)").mode).toBe("advanced");
    // Step: every 5 minutes via cron syntax.
    expect(parseExpression("cron(*/5 * * * ? *)").mode).toBe("advanced");
  });
});

describe("cron-builder: nextOccurrences", () => {
  // Pin "now" to a known UTC instant so tests aren't flaky.
  const NOW = new Date("2026-04-21T12:00:00Z");

  it("rate(5 minutes) → +5, +10, +15, +20, +25 min", () => {
    const runs = nextOccurrences("rate(5 minutes)", 5, NOW);
    expect(runs).toHaveLength(5);
    expect(runs[0].getTime() - NOW.getTime()).toBeGreaterThanOrEqual(5 * 60_000);
    expect(runs[4].getTime() - NOW.getTime()).toBeLessThan(26 * 60_000);
  });

  it("daily at 09:00 UTC → tomorrow 09:00 next", () => {
    const runs = nextOccurrences("cron(0 9 * * ? *)", 1, NOW);
    expect(runs).toHaveLength(1);
    // NOW is 12:00 UTC same day → next fire is tomorrow 09:00 UTC.
    expect(runs[0].toISOString()).toBe("2026-04-22T09:00:00.000Z");
  });

  it("hourly at :30 → next is 12:30 same day", () => {
    const runs = nextOccurrences("cron(30 * * * ? *)", 1, NOW);
    expect(runs[0].toISOString()).toBe("2026-04-21T12:30:00.000Z");
  });

  it("weekly Mon 09:00 → the next Monday at 09:00", () => {
    // 2026-04-21 is Tue → next Mon is 2026-04-27.
    const runs = nextOccurrences("cron(0 9 ? * 2 *)", 1, NOW);
    expect(runs[0].toISOString()).toBe("2026-04-27T09:00:00.000Z");
  });

  it("monthly day 15 at 00:00 → next 15th", () => {
    // NOW is 2026-04-21 → next is 2026-05-15.
    const runs = nextOccurrences("cron(0 0 15 * ? *)", 1, NOW);
    expect(runs[0].toISOString()).toBe("2026-05-15T00:00:00.000Z");
  });

  it("returns [] for advanced/unsupported expressions", () => {
    expect(nextOccurrences("cron(*/5 * * * ? *)", 3, NOW)).toEqual([]);
  });
});
