import type React from "react";
import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "../../../i18n";

beforeAll(async () => {
  await i18n.changeLanguage("en");
});

vi.mock("../../../hooks/useRuns", () => ({
  useRunList: () => ({
    runs: [
      {
        runId: "01JWXYZ-sched",
        trigger: "schedule",
        scheduleId: "agent-studio-agt-test-daily-report",
        status: "completed",
        input: "analyze metrics",
        model: "claude-sonnet",
        totalTokens: 5000,
        durationMs: 3000,
        artifactCount: 1,
        startedAt: "2026-04-20T08:00:00Z",
        completedAt: "2026-04-20T08:00:03Z",
      },
      {
        runId: "01JWXYZ-manual",
        trigger: "manual" as const,
        scheduleId: null,
        status: "completed" as const,
        input: "manual run test",
        model: "claude-sonnet",
        totalTokens: 200,
        durationMs: 800,
        artifactCount: 0,
        startedAt: "2026-04-20T09:00:00Z",
        completedAt: "2026-04-20T09:00:01Z",
      },
      {
        runId: "01JWXYZ-failed",
        trigger: "schedule",
        scheduleId: "agent-studio-agt-test-hourly",
        status: "failed",
        input: "test failed run",
        model: "claude-sonnet",
        totalTokens: 100,
        durationMs: 500,
        artifactCount: 0,
        startedAt: "2026-04-20T10:00:00Z",
        completedAt: "2026-04-20T10:00:01Z",
      },
    ],
    loading: false,
    error: null,
    refresh: vi.fn(),
  }),
  useRunDetail: () => ({
    detail: null,
    output: null,
    loading: false,
    error: null
  }),
}));

// TraceStatsStrip hits the API; stub it so the component renders cleanly.
vi.mock("../TraceStatsStrip", () => ({
  default: () => null,
}));

import RunsTab from "../RunsTab";

function renderRunsTab(props: Partial<React.ComponentProps<typeof RunsTab>> = {}) {
  return render(
    <I18nextProvider i18n={i18n}>
      <RunsTab agentId="agt-1" {...props} />
    </I18nextProvider>,
  );
}

describe("RunsTab", () => {
  it("renders the Runs title", async () => {
    renderRunsTab();
    expect(await screen.findByText("Runs")).toBeInTheDocument();
  });

  it("renders run items with different statuses", async () => {
    renderRunsTab();
    const list = await screen.findByTestId("runs-list");
    expect(list).toBeInTheDocument();
    expect(screen.getByText("analyze metrics")).toBeInTheDocument();
    expect(screen.getByText("manual run test")).toBeInTheDocument();
    expect(screen.getByText("test failed run")).toBeInTheDocument();
  });

  it("shows schedule vs manual badges correctly", async () => {
    renderRunsTab();
    // Schedule badge shows suffix
    expect(await screen.findByText(/Schedule · daily-report/)).toBeInTheDocument();
    // Manual badge
    expect(screen.getByText(/Manual/)).toBeInTheDocument();
  });

  it("shows status badges with different colors", async () => {
    renderRunsTab();
    const completedBadges = await screen.findAllByText("completed");
    expect(completedBadges.length).toBeGreaterThan(0);
    expect(screen.getByText("failed")).toBeInTheDocument();
  });
});
