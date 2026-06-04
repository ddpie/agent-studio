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

Stderr handling
---------------
kiro-cli-chat occasionally writes non-JSON log lines (setup banners,
telemetry pings) to stderr. We redirect stderr to a tempfile, then
drain the file after the process exits and forward each non-empty line
to our logger. That surfaces crash messages in CloudWatch without
racing a reader task during shutdown. The stdout demultiplexer still
drops non-JSON lines defensively, since old Kiro builds interleaved
the two streams.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from typing import Any

log = logging.getLogger(__name__)

# Budgets tuned for interactive Meta-Agent turns. `prompt` must be long
# enough to cover real tool-heavy turns (MCP round trips + LLM thinking);
# `request` is for the short control-plane verbs.
DEFAULT_REQUEST_TIMEOUT_S = 30.0
DEFAULT_PROMPT_TIMEOUT_S = 300.0


def _log_session_model(resp: dict, kind: str) -> None:
    """Extract whichever model-selection field Kiro returns in the response.

    ACP response shape (kiro-cli-chat 2.0.0) for session/new:
      {"result": {"sessionId": "...", "models": [...], "currentModelId": "...", ...}}
    session/load usually omits sessionId but carries the same models+current.
    Field names observed across builds: currentModelId / selectedModelId /
    activeModelId / model. Log whatever matches so we can verify the
    agent-config `model` field actually took effect.
    """
    if not isinstance(resp, dict):
        return
    result = resp.get("result") or {}
    if not isinstance(result, dict):
        return
    current = (
        result.get("currentModelId")
        or result.get("selectedModelId")
        or result.get("activeModelId")
        or result.get("model")
        or result.get("defaultModelId")
    )
    models = result.get("models")
    model_ids = []
    if isinstance(models, list):
        for m in models:
            if isinstance(m, dict):
                mid = m.get("id") or m.get("modelId")
                if mid:
                    model_ids.append(str(mid))
            elif isinstance(m, str):
                model_ids.append(m)
    log.warning(
        "acp %s: current_model=%r models_count=%d models=%s",
        kind, current, len(model_ids), model_ids[:10],
    )


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
        model_id: str | None = None,
    ):
        self._binary = binary
        self._home = kiro_home
        self._api_key = api_key
        self._agent_name = agent_name
        self._trust_all_tools = trust_all_tools
        self._extra_env = dict(extra_env or {})
        # Passed to `kiro-cli-chat acp --model <id>`. When set, overrides
        # whatever `model` the agent-config JSON carries. This is the
        # per-invocation Kiro-native switch — no need to rewrite
        # `/mnt/kiro/.kiro/agents/<name>.json` for each model change,
        # which was racy across concurrent turns on the same container.
        self._model_id = model_id

        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
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
        if self._model_id:
            args.extend(["--model", self._model_id])
        if self._trust_all_tools:
            args.append("--trust-all-tools")
        log.debug("spawning kiro acp: argv=%s", args[1:])

        # Route kiro-cli-chat stderr to a real file descriptor. We read it
        # back after the process exits and ship each line to our logger so
        # crashes and warnings show up in CloudWatch. An asyncio pipe
        # would also work, but a file lets us drain the buffer without
        # racing a reader task during shutdown.
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

        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reader_task = None

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
            except Exception as e:
                log.warning("failed to flush kiro stderr file: %s", e)
            self._stderr_file = None

        # The MCP stdio subprocess writes its own diagnostic log to
        # /tmp/mcp-stdio-<pid>.log. Tail whatever is there so a failure
        # inside the MCP server (import error, auth config, etc.) shows
        # up in the same CloudWatch stream. Best-effort; ignore missing
        # or empty files.
        import glob as _glob
        import os as _os_mod
        try:
            for path in sorted(_glob.glob("/tmp/mcp-stdio-*.log")):
                try:
                    with open(path, "rb") as f:
                        data = f.read()
                    for line in data.decode("utf-8", errors="replace").splitlines():
                        if line.strip():
                            log.warning("mcp-stdio[%s]: %s", path, line[:500])
                    _os_mod.unlink(path)
                except Exception as e:
                    log.warning("failed to tail %s: %s", path, e)
        except Exception:
            pass


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
                resp = await self._request(
                    "session/load",
                    {
                        "sessionId": saved_uuid,
                        "cwd": cwd,
                        "mcpServers": mcp_servers,
                    },
                )
                _log_session_model(resp, kind="session/load")
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
        _log_session_model(resp, kind="session/new")
        return resp["result"]["sessionId"], True

    async def list_models(self, cwd: str) -> list[dict[str, Any]]:
        """Fetch the `models` array Kiro advertises for this agent config.

        Kiro returns it in the `session/new` response. We open a throwaway
        session just to read the list and discard the session id — the
        frontend only needs model ids + labels, not a live conversation.
        """
        resp = await self._request(
            "session/new", {"cwd": cwd, "mcpServers": []}
        )
        result = resp.get("result") or {}
        models = result.get("models") or []
        return models if isinstance(models, list) else []

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
            # Debug aid: periodically log iterator state so we can tell
            # whether a slow turn is "Kiro thinking" (events_qsize steady at 0,
            # fut not done) vs "Kiro already finished but client hung" (fut
            # done, no events drained) vs "events flowing but nothing
            # translated upstream" (qsize > 0 but mapper drops them).
            _iter_start = asyncio.get_running_loop().time()
            _last_tick = _iter_start
            _events_seen = 0
            try:
                while not fut.done():
                    now = asyncio.get_running_loop().time()
                    remaining = deadline - now
                    if remaining <= 0:
                        fut.cancel()
                        raise asyncio.TimeoutError(
                            f"session/prompt exceeded {timeout_s}s"
                        )
                    # Tick log every 5s of quiet.
                    if now - _last_tick >= 5.0:
                        log.warning(
                            "acp prompt tick: elapsed=%.1fs events_qsize=%d "
                            "events_seen=%d fut_done=%s remaining=%.1fs",
                            now - _iter_start, self._events.qsize(),
                            _events_seen, fut.done(), remaining,
                        )
                        _last_tick = now
                    # Short wait so we periodically re-check fut.done(); the
                    # final response might land between events.
                    try:
                        event = await asyncio.wait_for(
                            self._events.get(), timeout=min(0.5, remaining)
                        )
                    except asyncio.TimeoutError:
                        continue
                    _events_seen += 1
                    yield event
                log.warning(
                    "acp prompt done: elapsed=%.1fs events_seen=%d fut_done=%s",
                    asyncio.get_running_loop().time() - _iter_start,
                    _events_seen, fut.done(),
                )
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
