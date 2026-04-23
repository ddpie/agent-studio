import { describe, it, expect } from "vitest";
import { _migrateLegacyMessage, _migrateToV3, type Message } from "../chat-store";

const baseMsg = (content: string, extras: Partial<Message> = {}): Message => ({
  id: "m1",
  role: "assistant",
  content,
  timestamp: 0,
  ...extras,
});

describe("_migrateLegacyMessage", () => {
  it("passes through plain prose unchanged", () => {
    const m = baseMsg("hello world");
    expect(_migrateLegacyMessage(m)).toBe(m);
  });

  it("strips tool-call <details> block from content", () => {
    const m = baseMsg(
      'here is the chart:\n\n<details class="tool-call"><summary>Called <strong>run_command</strong></summary>\n\nsome output\n</details>\n\ndone',
    );
    const out = _migrateLegacyMessage(m);
    expect(out.content).not.toMatch(/details/);
    expect(out.content).not.toMatch(/run_command/);
    expect(out.content).toContain("here is the chart");
    expect(out.content).toContain("done");
  });

  it("strips tool-rich-output div", () => {
    const m = baseMsg(
      'chart:\n\n<div class="tool-rich-output"><svg>...</svg></div>\n\ndone',
    );
    expect(_migrateLegacyMessage(m).content).not.toMatch(/svg/);
  });

  it("extracts __S3_DOWNLOAD__ into s3Downloads and strips marker from content", () => {
    const m = baseMsg(
      "Here is your chart.\n\n__S3_DOWNLOAD__:outputs/abc.png:chart.png\n\nCheers",
    );
    const out = _migrateLegacyMessage(m);
    expect(out.content).not.toContain("__S3_DOWNLOAD__");
    expect(out.content).toContain("Here is your chart");
    expect(out.content).toContain("Cheers");
    expect(out.s3Downloads).toEqual([{ key: "outputs/abc.png", filename: "chart.png" }]);
  });

  it("dedupes markers and preserves existing s3Downloads", () => {
    const m = baseMsg(
      "__S3_DOWNLOAD__:outputs/a.png:a.png __S3_DOWNLOAD__:outputs/a.png:a.png",
      { s3Downloads: [{ key: "outputs/a.png", filename: "a.png" }] },
    );
    const out = _migrateLegacyMessage(m);
    expect(out.s3Downloads).toHaveLength(1);
    expect(out.s3Downloads![0].key).toBe("outputs/a.png");
  });

  it("returns same reference when no legacy markers present", () => {
    const m = baseMsg("plain prose", {
      toolCalls: [{ id: "t1", name: "foo", input: "", output: "bar", isSvg: false }],
    });
    expect(_migrateLegacyMessage(m)).toBe(m);
  });

  it("never echoes legacy tool markers back into content (hallucination guard)", () => {
    // The root cause this refactor addresses: a model sees the
    // <details>/__S3_DOWNLOAD__ text in history and copies the format to
    // produce fake tool outputs. After migration neither must survive in
    // the content string that gets replayed to the model.
    const m = baseMsg(
      'intro\n<details class="tool-call"><summary>Called <strong>x</strong></summary>out</details>\nmiddle\n__S3_DOWNLOAD__:outputs/fake.png:fake.png\noutro',
    );
    const out = _migrateLegacyMessage(m);
    expect(out.content).not.toContain("<details");
    expect(out.content).not.toContain("__S3_DOWNLOAD__");
    expect(out.content).toContain("intro");
    expect(out.content).toContain("middle");
    expect(out.content).toContain("outro");
  });
});

describe("_migrateToV3", () => {
  it("moves v1/v2 flat messages under current agent key", () => {
    const out = _migrateToV3({
      currentAgentId: "agent-a",
      messages: [baseMsg("hello")],
      sessionId: "sess-x",
      activeSessionId: "sess-y",
      selectedModelId: "model-z",
      sessions: [],
      lastActiveSessionByAgent: {},
    });
    expect(out.messagesByAgent).toEqual({ "agent-a": [baseMsg("hello")] });
    expect(out.sessionIdByAgent).toEqual({ "agent-a": "sess-x" });
    expect(out.activeSessionByAgent).toEqual({ "agent-a": "sess-y" });
    expect(out.selectedModelByAgent).toEqual({ "agent-a": "model-z" });
    expect(out.currentAgentId).toBe("agent-a");
  });

  it("uses 'meta' as bucket for null currentAgentId", () => {
    const out = _migrateToV3({
      currentAgentId: null,
      messages: [baseMsg("hi meta")],
    });
    expect(out.messagesByAgent).toEqual({ meta: [baseMsg("hi meta")] });
  });

  it("preserves existing v3 per-agent buckets and does not overwrite", () => {
    const out = _migrateToV3({
      currentAgentId: "agent-a",
      messagesByAgent: { "agent-a": [baseMsg("already here")] },
      messages: [baseMsg("should be ignored")],
    });
    expect(out.messagesByAgent).toEqual({ "agent-a": [baseMsg("already here")] });
  });

  it("also applies legacy marker cleanup per-message during v3 migration", () => {
    const out = _migrateToV3({
      currentAgentId: "agent-a",
      messages: [
        baseMsg(
          'text with __S3_DOWNLOAD__:outputs/k.png:k.png and more',
        ),
      ],
    });
    const migratedMsg = out.messagesByAgent!["agent-a"][0];
    expect(migratedMsg.content).not.toContain("__S3_DOWNLOAD__");
    expect(migratedMsg.s3Downloads).toEqual([{ key: "outputs/k.png", filename: "k.png" }]);
  });

  it("handles empty persisted state gracefully", () => {
    const out = _migrateToV3({});
    expect(out.messagesByAgent).toEqual({});
    expect(out.sessions).toEqual([]);
    expect(out.currentAgentId).toBe(null);
  });

  it("cleans legacy markers inside persisted sessions too", () => {
    const out = _migrateToV3({
      sessions: [
        {
          id: "s1",
          agentKey: "agent-a",
          title: "Chat",
          messages: [baseMsg('hi __S3_DOWNLOAD__:outputs/k.png:k.png')],
          createdAt: 1,
          updatedAt: 1,
        },
      ],
    });
    const sessMsg = out.sessions![0].messages[0];
    expect(sessMsg.content).not.toContain("__S3_DOWNLOAD__");
    expect(sessMsg.s3Downloads).toEqual([{ key: "outputs/k.png", filename: "k.png" }]);
  });
});
