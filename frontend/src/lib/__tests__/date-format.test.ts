import { describe, it, expect } from "vitest";
import { formatDateTime, formatDate, formatTimeShort } from "../date-format";

// All assertions check fallback / structural behavior rather than exact
// locale-formatted strings: Node's Intl output drifts between minor versions
// and across timezones (CI may run in UTC, dev in local). We pin the parsed
// instant by checking against the equivalent Date.toLocaleString() output —
// computing the expected string the same way the SUT does so we're robust
// across runners while still exercising the parse path.

describe("formatDateTime / parseBackendDate", () => {
  it("returns fallback for null/undefined/empty", () => {
    expect(formatDateTime(null)).toBe("—");
    expect(formatDateTime(undefined)).toBe("—");
    expect(formatDateTime("")).toBe("—");
    expect(formatDateTime("   ")).toBe("—");
  });

  it("uses custom fallback when provided", () => {
    expect(formatDateTime(null, "n/a")).toBe("n/a");
    expect(formatDateTime("not a date", "?")).toBe("?");
  });

  it("returns fallback for invalid date string", () => {
    expect(formatDateTime("not a date")).toBe("—");
  });

  it("accepts Date instances", () => {
    const d = new Date("2026-04-21T12:00:00Z");
    expect(formatDateTime(d)).toBe(d.toLocaleString());
  });

  it("returns fallback for invalid Date instances", () => {
    expect(formatDateTime(new Date("nonsense"))).toBe("—");
  });

  it("accepts numeric epoch ms", () => {
    const d = new Date(1_700_000_000_000);
    expect(formatDateTime(1_700_000_000_000)).toBe(d.toLocaleString());
  });

  it("returns fallback for NaN epoch", () => {
    expect(formatDateTime(Number.NaN)).toBe("—");
  });

  it("treats naive 'YYYY-MM-DD HH:MM:SS' strings as UTC, not local", () => {
    // The whole point of parseBackendDate: a backend-emitted naive timestamp
    // should mean the same instant regardless of the runner's timezone.
    const naive = "2026-04-21 12:00:00";
    const expected = new Date("2026-04-21T12:00:00Z").toLocaleString();
    expect(formatDateTime(naive)).toBe(expected);
  });

  it("preserves explicit UTC suffix (Z)", () => {
    const iso = "2026-04-21T12:00:00Z";
    expect(formatDateTime(iso)).toBe(new Date(iso).toLocaleString());
  });

  it("preserves explicit numeric offset", () => {
    const offset = "2026-04-21T12:00:00+02:00";
    expect(formatDateTime(offset)).toBe(new Date(offset).toLocaleString());
  });

  it("handles fractional seconds with no tz", () => {
    const naive = "2026-04-21 12:00:00.123";
    const expected = new Date("2026-04-21T12:00:00.123Z").toLocaleString();
    expect(formatDateTime(naive)).toBe(expected);
  });
});

describe("formatDate", () => {
  it("returns fallback for empty input", () => {
    expect(formatDate(null)).toBe("—");
    expect(formatDate("")).toBe("—");
  });

  it("formats with toLocaleDateString", () => {
    const iso = "2026-04-21T12:00:00Z";
    expect(formatDate(iso)).toBe(new Date(iso).toLocaleDateString());
  });

  it("uses custom fallback", () => {
    expect(formatDate(undefined, "n/a")).toBe("n/a");
  });

  it("treats naive string as UTC", () => {
    const naive = "2026-04-21 00:30:00";
    const expected = new Date("2026-04-21T00:30:00Z").toLocaleDateString();
    expect(formatDate(naive)).toBe(expected);
  });
});

describe("formatTimeShort", () => {
  it("returns fallback for empty input", () => {
    expect(formatTimeShort(null)).toBe("—");
    expect(formatTimeShort("")).toBe("—");
  });

  it("formats HH:MM (2-digit) via toLocaleTimeString", () => {
    const iso = "2026-04-21T12:00:00Z";
    const expected = new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    expect(formatTimeShort(iso)).toBe(expected);
  });

  it("uses custom fallback", () => {
    expect(formatTimeShort(undefined, "??:??")).toBe("??:??");
  });
});
