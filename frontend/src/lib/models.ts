/** Shared model definitions used across ChatPanel and EditAssistant */

interface ModelOption {
  id: string;
  label: string;
}

export interface ModelGroup {
  label: string;
  models: ModelOption[];
}

export const MODEL_GROUPS: ModelGroup[] = [
  {
    label: "Claude 4.7",
    models: [
      { id: "us.anthropic.claude-opus-4-7", label: "Opus 4.7 (US)" },
      { id: "global.anthropic.claude-opus-4-7", label: "Opus 4.7 (Global)" },
    ],
  },
  {
    label: "Claude 4.6",
    models: [
      { id: "us.anthropic.claude-opus-4-6-v1", label: "Opus 4.6 (US)" },
      { id: "global.anthropic.claude-opus-4-6-v1", label: "Opus 4.6 (Global)" },
      { id: "us.anthropic.claude-sonnet-4-6", label: "Sonnet 4.6 (US)" },
      { id: "global.anthropic.claude-sonnet-4-6", label: "Sonnet 4.6 (Global)" },
    ],
  },
  {
    label: "Claude 4.5",
    models: [
      { id: "us.anthropic.claude-opus-4-5-20251101-v1:0", label: "Opus 4.5 (US)" },
      { id: "global.anthropic.claude-opus-4-5-20251101-v1:0", label: "Opus 4.5 (Global)" },
      { id: "us.anthropic.claude-sonnet-4-5-20250929-v1:0", label: "Sonnet 4.5 (US)" },
      { id: "global.anthropic.claude-sonnet-4-5-20250929-v1:0", label: "Sonnet 4.5 (Global)" },
      { id: "us.anthropic.claude-haiku-4-5-20251001-v1:0", label: "Haiku 4.5 (US)" },
      { id: "global.anthropic.claude-haiku-4-5-20251001-v1:0", label: "Haiku 4.5 (Global)" },
    ],
  },
  {
    label: "Claude 4",
    models: [
      { id: "us.anthropic.claude-opus-4-1-20250805-v1:0", label: "Opus 4.1 (US)" },
      { id: "us.anthropic.claude-opus-4-20250514-v1:0", label: "Opus 4 (US)" },
      { id: "us.anthropic.claude-sonnet-4-20250514-v1:0", label: "Sonnet 4 (US)" },
      { id: "global.anthropic.claude-sonnet-4-20250514-v1:0", label: "Sonnet 4 (Global)" },
    ],
  },
  {
    label: "Claude 3.x",
    models: [
      { id: "us.anthropic.claude-3-7-sonnet-20250219-v1:0", label: "3.7 Sonnet (US)" },
      { id: "us.anthropic.claude-3-5-sonnet-20241022-v2:0", label: "3.5 Sonnet v2 (US)" },
      { id: "us.anthropic.claude-3-5-haiku-20241022-v1:0", label: "3.5 Haiku (US)" },
    ],
  },
  {
    label: "DeepSeek",
    models: [
      { id: "deepseek.v3.2", label: "DeepSeek V3.2" },
      { id: "us.deepseek.r1-v1:0", label: "DeepSeek R1 (US)" },
    ],
  },
  {
    label: "MiniMax",
    models: [
      { id: "minimax.minimax-m2.5", label: "MiniMax M2.5" },
      { id: "minimax.minimax-m2.1", label: "MiniMax M2.1" },
      { id: "minimax.minimax-m2", label: "MiniMax M2" },
    ],
  },
  {
    label: "Qwen",
    models: [
      { id: "qwen.qwen3-coder-next", label: "Qwen3 Coder Next" },
      { id: "qwen.qwen3-next-80b-a3b", label: "Qwen3 Next 80B A3B" },
      { id: "qwen.qwen3-coder-30b-a3b-v1:0", label: "Qwen3 Coder 30B A3B" },
      { id: "qwen.qwen3-32b-v1:0", label: "Qwen3 32B" },
    ],
  },
];

// Default to the `global.*` inference profile so us-east-1 and us-west-2
// stacks share the same default without a region branch.
export const DEFAULT_MODEL_ID =
  MODEL_GROUPS[0].models.find((m) => m.id.startsWith("global."))?.id ||
  MODEL_GROUPS[0].models[0].id;

/**
 * Default Kiro-native model id used for Meta-Agent chat. The Meta-Agent
 * runs on Kiro, which expects its own id format (not the Bedrock
 * inference-profile ids in MODEL_GROUPS). Keep this in sync with
 * `meta-agent/kiro_adapter/kiro_home.py::DEFAULT_MODEL`.
 */
export const DEFAULT_KIRO_MODEL_ID = "claude-opus-4.6";

const ALL_MODELS = MODEL_GROUPS.flatMap((g) => g.models);

export function findModelLabel(modelId: string): string {
  return ALL_MODELS.find((m) => m.id === modelId)?.label || "Select";
}
