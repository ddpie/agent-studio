import { describe, it, expect } from "vitest";
import { demoteHashForWorkspaceSwitch } from "../workspace-switch-url";

describe("demoteHashForWorkspaceSwitch", () => {
  it("leaves list and cross-workspace routes unchanged", () => {
    expect(demoteHashForWorkspaceSwitch("#/agents")).toBe("#/agents");
    expect(demoteHashForWorkspaceSwitch("#/skills")).toBe("#/skills");
    expect(demoteHashForWorkspaceSwitch("#/tools")).toBe("#/tools");
    expect(demoteHashForWorkspaceSwitch("#/mcp")).toBe("#/mcp");
    expect(demoteHashForWorkspaceSwitch("#/marketplace")).toBe("#/marketplace");
    expect(demoteHashForWorkspaceSwitch("#/marketplace/tools")).toBe("#/marketplace/tools");
    expect(demoteHashForWorkspaceSwitch("#/settings")).toBe("#/settings");
  });

  it("demotes workspace-scoped detail routes to their list ancestor", () => {
    expect(demoteHashForWorkspaceSwitch("#/agents/edit/abc123")).toBe("#/agents");
    expect(demoteHashForWorkspaceSwitch("#/agents/edit/abc/skills/xyz")).toBe("#/agents");
    expect(demoteHashForWorkspaceSwitch("#/agents/chat/abc")).toBe("#/agents");
    expect(demoteHashForWorkspaceSwitch("#/agents/abc123")).toBe("#/agents");
    expect(demoteHashForWorkspaceSwitch("#/agents/abc/runs/run1")).toBe("#/agents");
    expect(demoteHashForWorkspaceSwitch("#/skills/xyz")).toBe("#/skills");
    expect(demoteHashForWorkspaceSwitch("#/tools/mytool")).toBe("#/tools");
  });

  it("handles empty and root hashes", () => {
    expect(demoteHashForWorkspaceSwitch("")).toBe("");
    expect(demoteHashForWorkspaceSwitch("#")).toBe("");
    expect(demoteHashForWorkspaceSwitch("#/")).toBe("");
  });

  it("preserves query string on demotion", () => {
    expect(demoteHashForWorkspaceSwitch("#/agents/edit/abc?foo=bar")).toBe("#/agents?foo=bar");
    expect(demoteHashForWorkspaceSwitch("#/agents?foo=bar")).toBe("#/agents?foo=bar");
  });
});
