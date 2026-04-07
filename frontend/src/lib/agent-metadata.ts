import { fetchAgent, fetchAgentFile } from "./api-client";

export interface AgentSkillEntry {
  id: string;
  sourceSkillId: string;
  sourceContentHash: string;
  name: string;
  description: string;
  contentHash: string;
  files: string[];
}

export interface AgentMetadata {
  name: string;
  display_name: string;
  description: string;
  model_id: string;
  default_model_id: string;
  system_prompt: string;
  tool_definitions: string;
  tool_names: string;
  welcome_message: string;
  suggestions: string[];
  tools: string[];
  template_id: string;
  supports_images: boolean;
  created_at: string;
  updated_at: string;
  created_by: string;
  visibility: string;
  skills: AgentSkillEntry[];
  deployedSkillHashes?: Record<string, string>;
}

/**
 * 从 API 获取 agent 元数据。
 * DDB 字段 + S3 文件（system_prompt, tool_definitions）合并为 AgentMetadata。
 */
export async function fetchAgentMetadata(agentId: string): Promise<AgentMetadata | null> {
  try {
    const item = await fetchAgent(agentId);

    // 并行读取 S3 文件
    const [systemPrompt, toolDefinitions] = await Promise.all([
      fetchAgentFile(agentId, "system_prompt.txt").catch(() => ""),
      fetchAgentFile(agentId, "tool_definitions.py").catch(() => ""),
    ]);

    return {
      name: item.name || agentId,
      display_name: item.display_name || item.name || agentId,
      description: item.description || "",
      model_id: item.model_id || "",
      default_model_id: item.default_model_id || item.model_id || "",
      system_prompt: systemPrompt,
      tool_definitions: toolDefinitions,
      tool_names: typeof item.tool_names === "string" ? item.tool_names : Array.isArray(item.tool_names) ? item.tool_names.join(",") : "",
      welcome_message: item.welcome_message || "",
      suggestions: item.suggestions || [],
      tools: typeof item.tool_names === "string"
        ? item.tool_names.split(",").map((t: string) => t.trim()).filter(Boolean)
        : Array.isArray(item.tool_names) ? item.tool_names : [],
      template_id: item.template_id || "",
      supports_images: item.supports_images || false,
      created_at: item.created_at || "",
      updated_at: item.updated_at || "",
      created_by: item.created_by || "",
      visibility: item.visibility || "private",
      skills: item.skill_ids || [],
      deployedSkillHashes: item.deployedSkillHashes,
    } as AgentMetadata;
  } catch (err) {
    console.error("fetchAgentMetadata error:", err);
    return null;
  }
}
