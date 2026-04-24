"""JSON-RPC client over stdio to `kiro-cli acp`.

Manages the subprocess lifecycle and provides an async iterator over ACP
notifications.

Expected ACP methods used:
- initialize                   (sync request)
- session/new                  (sync request; returns sessionId, modes, models)
- session/prompt               (sync request; triggers a turn, fires notifications)
- session/cancel               (on user abort)

Notifications to forward:
- AgentMessageChunk            (streaming text)
- ToolCall                     (start of a tool use)
- ToolCallUpdate               (progress)
- ToolCall result content      (tool output — field TBD once we see real traffic)
- TurnEnd                      (turn complete)
- _kiro.dev/metadata           (context %, optional passthrough)
- _kiro.dev/subagent/list_update (optional passthrough)

Not implemented yet — skeleton only.
"""


class KiroACPClient:
    """Async wrapper around a kiro-cli acp subprocess."""

    def __init__(
        self,
        kiro_binary: str,
        kiro_home: str,
        api_key: str,
        agent_name: str = "meta-agent",
        model_id: str = "claude-opus-4.6",
        trust_all_tools: bool = True,
    ) -> None:
        raise NotImplementedError

    async def start(self) -> None:
        """Spawn the subprocess and send `initialize`."""
        raise NotImplementedError

    async def new_session(self, cwd: str) -> str:
        """Send `session/new`, return sessionId."""
        raise NotImplementedError

    async def prompt(self, session_id: str, user_message: str):
        """Send `session/prompt`, yield notifications until TurnEnd."""
        raise NotImplementedError

    async def cancel(self, session_id: str) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError
