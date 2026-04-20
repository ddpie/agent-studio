import { describe, it, expect } from "vitest";
import { inferRunSource } from "../run-source";

describe("inferRunSource", () => {
  it("returns scheduled for a sched-<suffix>-<isoDate> session id", () => {
    const r = inferRunSource("sched-daily-report-2026-04-20T08:00:00Z");
    expect(r.kind).toBe("scheduled");
    expect(r.icon).toBe("calendar");
  });

  it("returns manual for a sched-<suffix>-manual-<ts> session id (Run now)", () => {
    const r = inferRunSource("sched-daily-report-manual-1713600000");
    expect(r.kind).toBe("manual");
    expect(r.icon).toBe("user");
  });

  it("returns chat for a random uuid-shaped session id", () => {
    const r = inferRunSource("9d3c2f1a-4b5e-6789-abcd-ef0123456789");
    expect(r.kind).toBe("chat");
    expect(r.icon).toBe("chat");
  });

  it("returns chat for an arbitrary non-sched string", () => {
    const r = inferRunSource("some-other-id");
    expect(r.kind).toBe("chat");
  });

  it("attaches a label for each kind", () => {
    expect(inferRunSource("sched-x-2026-04-20").label).toMatch(/sched/i);
    expect(inferRunSource("sched-x-manual-1").label).toMatch(/manual|run now/i);
    expect(inferRunSource("uuid").label).toMatch(/chat/i);
  });
});
