import { describe, it, expect, vi, beforeEach } from "vitest";

// Mocks — must be declared before importing the SUT so vi.mock hoists correctly.
const fetchAgent = vi.fn();
const fetchAgentFile = vi.fn();
vi.mock("../api-client", () => ({
  fetchAgent: (...args: unknown[]) => fetchAgent(...args),
  fetchAgentFile: (...args: unknown[]) => fetchAgentFile(...args),
}));

import { fetchAgentMetadata, fetchAgentMetadataLight } from "../agent-metadata";

beforeEach(() => {
  fetchAgent.mockReset();
  fetchAgentFile.mockReset();
  // silence the console.error the SUT emits on the error paths so test
  // output stays readable.
  vi.spyOn(console, "error").mockImplementation(() => {});
});

describe("fetchAgentMetadataLight", () => {
  it("maps DDB item fields to AgentMetadata", async () => {
    fetchAgent.mockResolvedValue({
      name: "alpha",
      display_name: "Alpha Bot",
      description: "desc",
      model_id: "claude-x",
      tool_names: "a,b,c",
      welcome_message: "hi",
      suggestions: ["s1"],
      template_id: "t1",
      supports_images: true,
      created_at: "2026-04-21T00:00:00Z",
      visibility: "public",
      skills: [{ id: "sk1" } as never],
      mcp_targets: ["m"],
      runtime_type: "harness",
      harness_arn: "arn:aws:...",
    });
    const m = await fetchAgentMetadataLight("agt-1");
    expect(m).not.toBeNull();
    expect(m!.name).toBe("alpha");
    expect(m!.display_name).toBe("Alpha Bot");
    expect(m!.tool_names).toBe("a,b,c");
    expect(m!.tools).toEqual(["a", "b", "c"]);
    expect(m!.runtime_type).toBe("harness");
    expect(m!.skills).toEqual([{ id: "sk1" }]);
    expect(m!.mcp_targets).toEqual(["m"]);
    // Light variant never reads S3.
    expect(fetchAgentFile).not.toHaveBeenCalled();
  });

  it("falls back to agentId/sensible defaults when fields are missing", async () => {
    fetchAgent.mockResolvedValue({});
    const m = await fetchAgentMetadataLight("agt-2");
    expect(m).not.toBeNull();
    expect(m!.name).toBe("agt-2");
    expect(m!.display_name).toBe("agt-2");
    expect(m!.description).toBe("");
    expect(m!.system_prompt).toBe("");
    expect(m!.tools).toEqual([]);
    expect(m!.suggestions).toEqual([]);
    expect(m!.skills).toEqual([]);
    expect(m!.mcp_targets).toEqual([]);
    expect(m!.runtime_type).toBe("zip");
    expect(m!.visibility).toBe("private");
  });

  it("handles tool_names as array", async () => {
    fetchAgent.mockResolvedValue({ tool_names: ["x", "y"] });
    const m = await fetchAgentMetadataLight("agt-3");
    expect(m!.tool_names).toBe("x,y");
    expect(m!.tools).toEqual(["x", "y"]);
  });

  it("trims whitespace and drops empty tool names from comma-string", async () => {
    fetchAgent.mockResolvedValue({ tool_names: " a , , b " });
    const m = await fetchAgentMetadataLight("agt-4");
    expect(m!.tools).toEqual(["a", "b"]);
  });

  it("returns null and logs when the API throws", async () => {
    fetchAgent.mockRejectedValue(new Error("boom"));
    const m = await fetchAgentMetadataLight("agt-err");
    expect(m).toBeNull();
  });

  it("falls back to legacy skill_ids when skills is absent", async () => {
    fetchAgent.mockResolvedValue({ skill_ids: ["legacy-1"] });
    const m = await fetchAgentMetadataLight("agt-5");
    expect(m!.skills).toEqual(["legacy-1"]);
  });
});

describe("fetchAgentMetadata", () => {
  it("returns DDB system_prompt directly for harness runtime (no S3 reads)", async () => {
    fetchAgent.mockResolvedValue({
      runtime_type: "harness",
      system_prompt: "You are alpha.",
    });
    const m = await fetchAgentMetadata("agt-h");
    expect(m).not.toBeNull();
    expect(m!.runtime_type).toBe("harness");
    expect(m!.system_prompt).toBe("You are alpha.");
    expect(m!.tool_definitions).toBe("");
    expect(fetchAgentFile).not.toHaveBeenCalled();
  });

  it("uses DDB system_prompt for zip runtime when present (skips S3 prompt fetch)", async () => {
    fetchAgent.mockResolvedValue({
      runtime_type: "zip",
      system_prompt: "ddb prompt",
    });
    fetchAgentFile.mockImplementation(async (_id: string, file: string) => {
      if (file === "tool_definitions.py") return "def t(): pass";
      throw new Error("should not call this");
    });
    const m = await fetchAgentMetadata("agt-z1");
    expect(m!.system_prompt).toBe("ddb prompt");
    expect(m!.tool_definitions).toBe("def t(): pass");
    // Only tool_definitions.py was fetched.
    expect(fetchAgentFile).toHaveBeenCalledTimes(1);
    expect(fetchAgentFile).toHaveBeenCalledWith("agt-z1", "tool_definitions.py");
  });

  it("falls back to S3 system_prompt.txt for zip runtime when DDB field empty", async () => {
    fetchAgent.mockResolvedValue({ runtime_type: "zip", system_prompt: "" });
    fetchAgentFile.mockImplementation(async (_id: string, file: string) => {
      if (file === "system_prompt.txt") return "from s3";
      if (file === "tool_definitions.py") return "tools";
      throw new Error("unexpected file " + file);
    });
    const m = await fetchAgentMetadata("agt-z2");
    expect(m!.system_prompt).toBe("from s3");
    expect(m!.tool_definitions).toBe("tools");
    expect(fetchAgentFile).toHaveBeenCalledTimes(2);
  });

  it("treats S3 fetch failures as empty strings (catches in-flight)", async () => {
    fetchAgent.mockResolvedValue({ runtime_type: "zip" });
    fetchAgentFile.mockRejectedValue(new Error("404"));
    const m = await fetchAgentMetadata("agt-z3");
    expect(m!.system_prompt).toBe("");
    expect(m!.tool_definitions).toBe("");
  });

  it("defaults runtime_type to zip when missing", async () => {
    fetchAgent.mockResolvedValue({});
    fetchAgentFile.mockResolvedValue("");
    const m = await fetchAgentMetadata("agt-z4");
    expect(m!.runtime_type).toBe("zip");
  });

  it("returns null when fetchAgent rejects", async () => {
    fetchAgent.mockRejectedValue(new Error("network"));
    const m = await fetchAgentMetadata("agt-err");
    expect(m).toBeNull();
  });

  it("normalizes tools field from string", async () => {
    fetchAgent.mockResolvedValue({
      runtime_type: "harness",
      system_prompt: "p",
      tool_names: "x, y , z",
    });
    const m = await fetchAgentMetadata("agt-tools");
    expect(m!.tools).toEqual(["x", "y", "z"]);
    expect(m!.tool_names).toBe("x, y , z");
  });
});
