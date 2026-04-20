import type React from "react";
import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "../../../i18n";

beforeAll(async () => {
  await i18n.changeLanguage("en");
});

vi.mock("../../../hooks/useTraces", () => ({
  useTraceSessions: () => ({
    sessions: [
      { sessionId: "sched-daily-report-2026-04-20T08:00:00Z", firstEvent: "2026-04-20T08:00:00Z", spanCount: 3, status: "OK", durationMs: 1200, totalTokens: 500 },
      { sessionId: "sched-daily-report-manual-1713600000", firstEvent: "2026-04-20T09:00:00Z", spanCount: 2, status: "OK", durationMs: 800, totalTokens: 200 },
      { sessionId: "9d3c2f1a-4b5e-6789-abcd-ef0123456789", firstEvent: "2026-04-20T10:00:00Z", spanCount: 4, status: "OK", durationMs: 1500, totalTokens: 900 },
    ],
    loading: false,
    error: null,
    refresh: vi.fn(),
  }),
  useSessionTrace: () => ({ root: null, loading: false, pending: false, error: null }),
  useSessionOutput: () => ({ data: null, loading: false, error: null }),
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
  it("renders the Runs title (not Traces)", async () => {
    renderRunsTab();
    expect(await screen.findByText("Runs")).toBeInTheDocument();
  });

  it("renders a source badge for each row", async () => {
    renderRunsTab();
    expect(await screen.findByTestId("run-source-sched-daily-report-2026-04-20T08:00:00Z")).toHaveTextContent(/scheduled/i);
    expect(screen.getByTestId("run-source-sched-daily-report-manual-1713600000")).toHaveTextContent(/manual/i);
    expect(screen.getByTestId("run-source-9d3c2f1a-4b5e-6789-abcd-ef0123456789")).toHaveTextContent(/chat/i);
  });

  it("fires onSelect when a row is clicked", async () => {
    const onSelect = vi.fn();
    renderRunsTab({ onSelect });
    const row = await screen.findByTestId("session-row-9d3c2f1a-4b5e-6789-abcd-ef0123456789");
    fireEvent.click(row);
    expect(onSelect).toHaveBeenCalledWith("9d3c2f1a-4b5e-6789-abcd-ef0123456789");
  });
});
