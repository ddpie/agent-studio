import { useEffect, useState, useCallback } from "react";
import { useAgentListStore } from "../../stores/agent-list-store";
import { useChatStore } from "../../stores/chat-store";
import { useAgentEditStore } from "../../stores/agent-edit-store";
import { Bot, RefreshCw, Loader2, MessageSquare, Settings2, Archive, ChevronDown } from "lucide-react";
import ConfirmDialog from "../ui/ConfirmDialog";

export default function AgentList({ collapsed = false }: { collapsed?: boolean }) {
  const { agents, archivedAgents, loading, fetchAgents } = useAgentListStore();
  const { targetAgentId, setTarget } = useChatStore();
  const { editingAgentId, openEdit, closeEdit, hasChanges } = useAgentEditStore();
  const [pendingAction, setPendingAction] = useState<(() => void) | null>(null);
  const [showArchived, setShowArchived] = useState(false);

  useEffect(() => {
    fetchAgents();
  }, [fetchAgents]);

  const handleSwitch = useCallback((action: () => void) => {
    if (editingAgentId && hasChanges()) {
      setPendingAction(() => action);
    } else {
      if (editingAgentId) closeEdit();
      action();
    }
  }, [editingAgentId, hasChanges, closeEdit]);

  if (collapsed) {
    return (
      <div className="flex flex-col items-center h-full py-3 gap-2">
        {/* Meta-Agent icon */}
        <button
          onClick={() => handleSwitch(() => { closeEdit(); setTarget(null, null); })}
          className={`w-9 h-9 rounded-lg flex items-center justify-center transition-colors ${
            targetAgentId === null && !editingAgentId
              ? "bg-blue-100 text-blue-600"
              : "text-gray-400 hover:bg-gray-200 hover:text-gray-600"
          }`}
          title="Meta Agent"
        >
          <MessageSquare className="w-4 h-4" />
        </button>

        {agents.length > 0 && <div className="w-5 border-t border-gray-300" />}

        {agents.map((agent) => (
          <div key={agent.id} className="relative group flex flex-col items-center">
            <div className="relative">
              <button
                onClick={() => handleSwitch(() => { closeEdit(); setTarget(agent.id, agent.displayName); })}
                className={`w-9 h-9 rounded-lg flex items-center justify-center text-xs font-bold transition-colors ${
                  (targetAgentId === agent.id && !editingAgentId) || editingAgentId === agent.id
                    ? "bg-blue-100 text-blue-600"
                    : "text-gray-400 hover:bg-gray-200 hover:text-gray-600"
                }`}
                title={agent.displayName}
              >
                {agent.displayName.charAt(0).toUpperCase()}
              </button>
              <span className={`absolute -bottom-0.5 -right-0.5 w-2 h-2 rounded-full border border-white ${agent.status === "READY" ? "bg-green-500" : "bg-yellow-500"}`} />
            </div>
            <span className="text-[9px] text-gray-400 leading-tight text-center w-12 mt-0.5 line-clamp-2 break-all">
              {agent.displayName}
            </span>
            {/* Edit icon on hover */}
            <button
              onClick={(e) => { e.stopPropagation(); openEdit(agent.id, agent.displayName); }}
              className="absolute -top-1 -right-1 w-4 h-4 bg-white border border-gray-200 rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity shadow-sm"
              title="Edit"
            >
              <Settings2 className="w-2.5 h-2.5 text-gray-400" />
            </button>
          </div>
        ))}

        <div className="flex-1" />
        <button
          onClick={fetchAgents}
          disabled={loading}
          className="w-9 h-9 rounded-lg flex items-center justify-center text-gray-400 hover:bg-gray-200"
          title="Refresh"
        >
          {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
        </button>

        <ConfirmDialog
          open={!!pendingAction}
          title="Unsaved changes"
          message="You have unsaved changes in the editor. Discard and switch?"
          confirmLabel="Discard"
          cancelLabel="Stay"
          danger
          onConfirm={() => { pendingAction?.(); setPendingAction(null); }}
          onCancel={() => setPendingAction(null)}
        />
      </div>
    );
  }

  // Full width mode
  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200">
        <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300">My Agents</h3>
        <button
          onClick={fetchAgents}
          disabled={loading}
          className="p-1.5 text-gray-400 hover:text-gray-600 rounded hover:bg-gray-100"
          title="Refresh"
        >
          {loading ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <RefreshCw className="w-3.5 h-3.5" />
          )}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {/* Meta-Agent */}
        <button
          onClick={() => handleSwitch(() => { closeEdit(); setTarget(null, null); })}
          className={`w-full text-left p-3 rounded-lg border transition-colors ${
            targetAgentId === null && !editingAgentId
              ? "border-blue-500 bg-blue-50"
              : "border-gray-200 hover:bg-gray-50"
          }`}
        >
          <div className="flex items-center gap-2">
            <MessageSquare className="w-4 h-4 text-blue-600 flex-shrink-0" />
            <div className="min-w-0">
              <p className="text-sm font-medium text-gray-900">Meta Agent</p>
              <p className="text-xs text-gray-500 truncate">Create & manage agents</p>
            </div>
          </div>
        </button>

        {agents.length > 0 && (
          <div className="text-xs text-gray-400 px-1 pt-2">Created Agents</div>
        )}

        {agents.map((agent) => (
          <div
            key={agent.id}
            className={`group w-full text-left p-3 rounded-lg border transition-colors ${
              (targetAgentId === agent.id && !editingAgentId) || editingAgentId === agent.id
                ? "border-blue-500 bg-blue-50"
                : "border-gray-200 hover:bg-gray-50"
            }`}
          >
            <div className="flex items-center justify-between">
              <button
                className="flex-1 text-left min-w-0"
                onClick={() => handleSwitch(() => { closeEdit(); setTarget(agent.id, agent.displayName); })}
              >
                <span className="text-sm font-medium text-gray-900 truncate block">
                  {agent.displayName}
                </span>
              </button>
              <div className="flex items-center gap-1 flex-shrink-0">
                <button
                  onClick={(e) => { e.stopPropagation(); openEdit(agent.id, agent.displayName); }}
                  className="p-1 text-gray-300 hover:text-blue-600 rounded transition-colors"
                  title="Edit agent"
                >
                  <Settings2 className="w-3.5 h-3.5" />
                </button>
                <span
                  className={`w-2 h-2 rounded-full ${
                    agent.status === "READY" ? "bg-green-500" : "bg-yellow-500"
                  }`}
                  title={agent.status}
                />
              </div>
            </div>
            {agent.description && (
              <p className="text-xs text-gray-500 mt-1 truncate">
                {agent.description}
              </p>
            )}
          </div>
        ))}

        {agents.length === 0 && !loading && (
          <div className="text-center text-gray-400 text-xs mt-4">
            <Bot className="w-6 h-6 mx-auto mb-1 opacity-30" />
            <p>No agents yet</p>
          </div>
        )}

        {/* Archived agents */}
        {archivedAgents.length > 0 && (
          <>
            <button
              onClick={() => setShowArchived(!showArchived)}
              className="flex items-center gap-1 text-xs text-gray-400 px-1 pt-3 hover:text-gray-600"
            >
              <ChevronDown className={`w-3 h-3 transition-transform ${showArchived ? "" : "-rotate-90"}`} />
              <Archive className="w-3 h-3" />
              Archived ({archivedAgents.length})
            </button>
            {showArchived && archivedAgents.map((agent) => (
              <div key={agent.id} className="w-full text-left p-2.5 rounded-lg border border-dashed border-gray-200 opacity-60">
                <div className="flex items-center justify-between">
                  <span className="text-xs text-gray-500 truncate">{agent.displayName}</span>
                  <span className="text-[10px] text-gray-400">archived</span>
                </div>
              </div>
            ))}
          </>
        )}
      </div>

      <ConfirmDialog
        open={!!pendingAction}
        title="Unsaved changes"
        message="You have unsaved changes in the editor. Discard and switch?"
        confirmLabel="Discard"
        cancelLabel="Stay"
        danger
        onConfirm={() => { pendingAction?.(); setPendingAction(null); }}
        onCancel={() => setPendingAction(null)}
      />
    </div>
  );
}
