import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { useRef } from "react";
import DetailSideNav, { type NavEntry } from "../DetailSideNav";

function Harness({ items }: { items: NavEntry[] }) {
  const ref = useRef<HTMLDivElement>(null);
  return (
    <div ref={ref}>
      <DetailSideNav items={items} activeId={null} scrollRootRef={ref} memoryKey="ws-1/agt-1" />
    </div>
  );
}

describe("DetailSideNav", () => {
  beforeEach(() => window.sessionStorage.clear());

  it("renders flat items unchanged", () => {
    render(<Harness items={[{ id: "a", label: "Alpha" }, { id: "b", label: "Beta" }]} />);
    expect(screen.getByTestId("nav-a")).toBeInTheDocument();
    expect(screen.getByTestId("nav-b")).toBeInTheDocument();
  });

  it("renders a collapsible group with its header and hides children when collapsed", () => {
    const items: NavEntry[] = [
      { id: "top", label: "Top" },
      {
        type: "group",
        id: "advanced",
        label: "Advanced",
        items: [
          { id: "x", label: "Xray" },
          { id: "y", label: "Yams" },
        ],
      },
    ];
    render(<Harness items={items} />);
    expect(screen.getByTestId("nav-group-advanced")).toBeInTheDocument();
    // Collapsed by default: child buttons are NOT rendered.
    expect(screen.queryByTestId("nav-x")).not.toBeInTheDocument();
    // Click the group header to expand.
    fireEvent.click(screen.getByTestId("nav-group-toggle-advanced"));
    expect(screen.getByTestId("nav-x")).toBeInTheDocument();
    expect(screen.getByTestId("nav-y")).toBeInTheDocument();
  });

  it("persists group open state to sessionStorage per memory key", () => {
    const items: NavEntry[] = [
      { type: "group", id: "advanced", label: "Advanced", items: [{ id: "x", label: "Xray" }] },
    ];
    const { unmount } = render(<Harness items={items} />);
    fireEvent.click(screen.getByTestId("nav-group-toggle-advanced"));
    expect(window.sessionStorage.getItem("agent-studio.detail-group.ws-1/agt-1:advanced")).toBe("1");
    unmount();
    render(<Harness items={items} />);
    // Still expanded on re-mount.
    expect(screen.getByTestId("nav-x")).toBeInTheDocument();
  });
});
