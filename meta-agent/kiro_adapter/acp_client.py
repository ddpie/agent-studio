"""JSON-RPC 2.0 client over stdio to `kiro-cli-chat acp`.

Manages the Kiro subprocess lifecycle for one Meta-Agent invocation and
exposes the ACP verbs we actually use. Everything else on the ACP surface
(prompts/, commands/, _kiro.dev/* notifications) flows through as opaque
events so sse_mapper.py can translate them.

Verbs covered (signatures verified on kiro-cli-chat 2.0.0 via live probe):

  initialize     — once after spawn
  session/new    — first turn of a conversation
  session/load   — subsequent turns; fallback to session/new on error
  session/prompt — drive the LLM for one turn, yields session/update events

Event stream
------------
Kiro mixes responses (id-bearing) and notifications (method-only) on the
same stdout. A background `_reader` task demultiplexes:

  - id-match → complete the matching pending future
  - else     → push onto the events queue for the current `prompt()` to
               consume

ACP sessions are serial by nature — one prompt in flight at a time — so
the event queue has one logical consumer. If that invariant ever needs to
relax, replace the queue with per-call queues keyed by request id.

Stderr merging
--------------
kiro-cli-chat occasionally writes non-JSON log lines (setup banners,
telemetry pings). We merge stderr into stdout and drop any line that
doesn't start with '{'. Verified in the EC2 probe that this does not
drop any real JSON-RPC frames.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, AsyncIterator

log = logging.getLogger(__name__)

# Budgets tuned for interactive Meta-Agent turns. `prompt` must be long
# enough to cover real tool-heavy turns (MCP round trips + LLM thinking);
# `request` is for the short control-plane verbs.
DEFAULT_REQUEST_TIMEOUT_S = 30.0
DEFAULT_PROMPT_TIMEOUT_S = 300.0


class ACPError(RuntimeError):
    """A JSON-RPC error response from kiro-cli-chat."""

    def __init__(self, method: str, code: int, message: str, data: Any = None):
        super().__init__(f"{method} -> {code} {message}")
        self.method = method
        self.code = code
        self.message = message
        self.data = data


class KiroACPClient:
    """Async driver for a single `kiro-cli-chat acp` subprocess.

    Typical usage (from main.py):

        client = KiroACPClient(binary, home, api_key, agent_name="meta-agent")
        await client.start()
        sid, is_new = await client.ensure_session(
            saved_uuid=saved, cwd="/mnt/kiro", mcp_servers=[]
        )
        async for event in client.prompt(sid, user_message):
            yield from sse_mapper.translate(event)
        await client.close()

    The client is one-shot per invocation. Do not reuse across turns on
    the same asyncio loop iteration — spawn a fresh one. (Kiro itself
    doesn't care, but the Meta-Agent entrypoint is per-invocation anyway.)
    """

    def __init__(
        self,
        binary: str,
        kiro_home: str,
        api_key: str,
        agent_name: str = "meta-agent",
        trust_all_tools: bool = True,
        extra_env: dict[str, str] | None = None,
    ):
        self._binary = binary
        self._home = kiro_home
        self._api_key = api_key
        self._agent_name = agent_name
        self._trust_all_tools = trust_all_tools
        self._extra_env = dict(extra_env or {})

        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        # Unbounded; Kiro fires a handful of notifications per turn, never
        # approaches any practical memory bound.
        self._events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._closed = False

    # ---- lifecycle --------------------------------------------------------

    async def start(self) -> None:
        """Spawn kiro-cli-chat acp and complete the `initialize` handshake."""
        if self._proc is not None:
            raise RuntimeError("client already started")

        env = os.environ.copy()
        env["HOME"] = self._home
        env["KIRO_API_KEY"] = self._api_key
        env.update(self._extra_env)

        args = [self._binary, "acp", "--agent", self._agent_name]
        if self._trust_all_tools:
            args.append("--trust-all-tools")

        # Route kiro-cli-chat stderr to a real file descriptor (not an
        # asyncio pipe). On AgentCore, piping kiro's stderr through
        # asyncio triggered "Bad file descriptor (os error 9)" at
        # startup; a plain on-disk file matches what a TTY-like fd would
        # look like and avoids that. We read the file back after the
        # process exits to expose crashes in CloudWatch.
        import tempfile as _tempfile
        self._stderr_file = _tempfile.NamedTemporaryFile(
            mode="wb+", prefix="kiro-cli-stderr-", delete=False
        )
        self._proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=self._stderr_file.fileno(),
            env=env,
        )

        self._reader_task = asyncio.create_task(
            self._reader(), name="kiro-acp-reader"
        )

        # Handshake. Protocol version 1 matches what we observed from
        # the 2.0.0 probe — a 1:1 server.
        await self._request(
            "initialize",
            {"protocolVersion": 1, "clientCapabilities": {}},
        )

    async def close(self) -> None:
        """Terminate the subprocess and cancel the reader."""
        if self._closed:
            return
        self._closed = True

        # Fail any outstanding requests so callers don't hang.
        exc = RuntimeError("KiroACPClient closed")
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()

        for task_attr in ("_reader_task", "_stderr_task"):
            task = getattr(self, task_attr, None)
            if task:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
                setattr(self, task_attr, None)

        if self._proc and self._proc.returncode is None:
            try:
                if self._proc.stdin and not self._proc.stdin.is_closing():
                    self._proc.stdin.close()
            except Exception:
                pass
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except (ProcessLookupError, asyncio.TimeoutError):
                try:
                    self._proc.kill()
                except ProcessLookupError:
                    pass

        # Surface whatever kiro wrote to its stderr file so crashes are
        # debuggable in CloudWatch.
        sf = getattr(self, "_stderr_file", None)
        if sf is not None:
            try:
                sf.close()
                import os as _os_mod
                with open(sf.name, "rb") as f:
                    data = f.read()
                if data:
                    for line in data.decode("utf-8", errors="replace").splitlines():
                        if line.strip():
                            log.warning("kiro-cli stderr: %s", line[:500])
                _os_mod.unlink(sf.name)
            except Exception as e:  # noqa: BLE001
                log.warning("failed to flush kiro stderr file: %s", e)
            self._stderr_file = None

    # ---- verbs ------------------------------------------------------------

    async def ensure_session(
        self,
        saved_uuid: str | None,
        cwd: str,
        mcp_servers: list[Any] | None = None,
    ) -> tuple[str, bool]:
        """Get a usable session id.

        Tries `session/load` if a uuid was saved from a previous turn. On
        any ACP error (typically "Session not found" after a runtime
        version bump wiped the session store) falls back silently to
        `session/new`. The caller is expected to persist the returned id
        via kiro_home.save_kiro_session_uuid().

        Returns:
            (session_id, is_new) — is_new=True means the caller should
            prepend any continuity context (e.g. a replayed history blob)
            on the first user turn.
        """
        mcp_servers = mcp_servers or []

        if saved_uuid:
            try:
                # session/load returns `modes` and `models` but *not*
                # sessionId — the caller supplied it. Reuse the input uuid.
                await self._request(
                    "session/load",
                    {
                        "sessionId": saved_uuid,
                        "cwd": cwd,
                        "mcpServers": mcp_servers,
                    },
                )
                return saved_uuid, False
            except ACPError as e:
                log.warning(
                    "session/load failed (%s), falling back to session/new: %s",
                    saved_uuid,
                    e,
                )

        resp = await self._request(
            "session/new",
            {"cwd": cwd, "mcpServers": mcp_servers},
        )
        return resp["result"]["sessionId"], True

    def prompt(
        self,
        session_id: str,
        user_text: str,
        timeout_s: float = DEFAULT_PROMPT_TIMEOUT_S,
    ) -> AsyncIterator[dict[str, Any]]:
        """Send one user turn; return an async iterator of ACP notifications.

        Yields every non-response message Kiro emits during the turn —
        `session/update` chunks (agent_message_chunk, tool_call,
        tool_call_update, turn_end, etc.) plus `_kiro.dev/*` metadata
        notifications. sse_mapper.py picks the ones it needs.

        Raises ACPError if Kiro returns an error response for session/prompt,
        or asyncio.TimeoutError on no completion within timeout_s.
        """
        # Drain any straggler events from prior verbs (e.g. the
        # commands/available notification Kiro fires right after
        # session/new). They are not part of this turn.
        self._drain_events()

        req_id = self._alloc_id()
        fut: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[req_id] = fut

        send_task = asyncio.create_task(
            self._write(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "method": "session/prompt",
                    "params": {
                        "sessionId": session_id,
                        "prompt": [{"type": "text", "text": user_text}],
                    },
                }
            )
        )

        async def _iter() -> AsyncIterator[dict[str, Any]]:
            await send_task
            deadline = asyncio.get_running_loop().time() + timeout_s
            try:
                while not fut.done():
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        fut.cancel()
                        raise asyncio.TimeoutError(
                            f"session/prompt exceeded {timeout_s}s"
                        )
                    # Short wait so we periodically re-check fut.done(); the
                    # final response might land between events.
                    try:
                        event = await asyncio.wait_for(
                            self._events.get(), timeout=min(0.5, remaining)
                        )
                    except asyncio.TimeoutError:
                        continue
                    yield event
            finally:
                self._pending.pop(req_id, None)

            if fut.done() and not fut.cancelled():
                resp = fut.result()
                err = resp.get("error")
                if err:
                    raise ACPError(
                        "session/prompt",
                        err.get("code", -1),
                        err.get("message", ""),
                        err.get("data"),
                    )

        return _iter()

    # ---- internals --------------------------------------------------------

    def _alloc_id(self) -> int:
        i = self._next_id
        self._next_id += 1
        return i

    def _drain_events(self) -> None:
        while True:
            try:
                self._events.get_nowait()
            except asyncio.QueueEmpty:
                return

    async def _write(self, msg: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("KiroACPClient not started")
        line = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        self._proc.stdin.write(line)
        await self._proc.stdin.drain()

    async def _request(
        self,
        method: str,
        params: dict[str, Any],
        timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S,
    ) -> dict[str, Any]:
        req_id = self._alloc_id()
        fut: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[req_id] = fut
        try:
            await self._write(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "method": method,
                    "params": params,
                }
            )
            resp = await asyncio.wait_for(fut, timeout=timeout_s)
        finally:
            self._pending.pop(req_id, None)

        err = resp.get("error")
        if err:
            raise ACPError(
                method,
                err.get("code", -1),
                err.get("message", ""),
                err.get("data"),
            )
        return resp

    async def _stderr_reader(self) -> None:
        """Forward kiro-cli-chat stderr to our logger.

        Runs in parallel with _reader, which handles stdout. Every line
        from Kiro's stderr lands in CloudWatch at warning level so auth
        failures, sqlite errors, crash messages etc. are visible.
        """
        assert self._proc is not None and self._proc.stderr is not None
        stderr = self._proc.stderr
        while True:
            raw = await stderr.readline()
            if not raw:
                return
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                log.warning("kiro-cli stderr: %s", line[:500])

    async def _reader(self) -> None:
        """Demultiplex stdout lines into pending-responses vs events."""
        assert self._proc is not None and self._proc.stdout is not None
        stdout = self._proc.stdout
        while True:
            raw = await stdout.readline()
            if not raw:
                # EOF — process exited. Fail pending futures so waiters
                # don't hang.
                exc = RuntimeError("kiro-cli-chat exited unexpectedly")
                for fut in list(self._pending.values()):
                    if not fut.done():
                        fut.set_exception(exc)
                return
            line = raw.decode("utf-8", errors="replace").strip()
            # Filter non-JSON stderr noise that got merged in. Log it at
            # warning level so real errors (auth failures, crashes) show
            # up in CloudWatch instead of getting silently dropped.
            if not line.startswith("{"):
                if line:
                    log.warning("kiro-cli stderr: %s", line[:500])
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                log.warning("acp: dropping non-json line: %s", line[:200])
                continue

            req_id = msg.get("id") if isinstance(msg, dict) else None
            if isinstance(req_id, int) and req_id in self._pending:
                fut = self._pending[req_id]
                if not fut.done():
                    fut.set_result(msg)
                continue
            # Notification (no id, or id of an already-resolved request)
            await self._events.put(msg)
