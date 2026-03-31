import { Plug } from "lucide-react";

export default function McpPage() {
  return (
    <div className="flex flex-col items-center justify-center h-full text-gray-400">
      <Plug className="w-12 h-12 mb-4 opacity-30" />
      <h2 className="text-lg font-medium text-gray-600">MCP Servers</h2>
      <p className="text-sm mt-1">Connect agents to external tools</p>
      <p className="text-xs mt-4 text-gray-300">Coming soon</p>
    </div>
  );
}
