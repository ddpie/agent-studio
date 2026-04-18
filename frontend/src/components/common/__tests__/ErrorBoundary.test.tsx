import React from "react";
import { describe, it, expect, beforeAll, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "../../../i18n";
import PageErrorBoundary from "../PageErrorBoundary";
import RootErrorBoundary from "../RootErrorBoundary";

function Boom(): never {
  throw new Error("boom");
}

beforeAll(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});

function wrap(children: React.ReactNode) {
  return <I18nextProvider i18n={i18n}>{children}</I18nextProvider>;
}

describe("PageErrorBoundary", () => {
  it("shows fallback and retry on error", () => {
    render(
      wrap(
        <PageErrorBoundary>
          <Boom />
        </PageErrorBoundary>
      )
    );
    expect(screen.getByText(/page couldn|页面加载失败/i)).toBeInTheDocument();
    expect(screen.getAllByRole("button").length).toBeGreaterThan(0);
  });

  it("renders children when no error", () => {
    render(
      wrap(
        <PageErrorBoundary>
          <span>hello</span>
        </PageErrorBoundary>
      )
    );
    expect(screen.getByText("hello")).toBeInTheDocument();
  });
});

describe("RootErrorBoundary", () => {
  it("shows reload on error", () => {
    render(
      wrap(
        <RootErrorBoundary>
          <Boom />
        </RootErrorBoundary>
      )
    );
    expect(screen.getByText(/something went wrong|出现错误/i)).toBeInTheDocument();
  });
});
