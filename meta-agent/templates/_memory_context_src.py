"""MemoryContext — runtime wrapper around AgentCore Memory data-plane.

This file is developed as regular Python so it can be unit-tested, then
its source is appended to BUILTIN_TOOLS_CODE in agent_template_v2.py
at zip-assembly time. Keep imports limited to stdlib + boto3 + strands
(which the deployed Agent already has).
"""
import asyncio
import json
import logging
import time

import boto3
from strands import tool as _tool

_log = logging.getLogger("memory_context")

_MEMORY_CLIENT = None


def _get_memory_client():
    global _MEMORY_CLIENT
    if _MEMORY_CLIENT is None:
        import os
        region = os.environ.get("AWS_REGION", "us-east-1")
        _MEMORY_CLIENT = boto3.client("bedrock-agentcore", region_name=region)
    return _MEMORY_CLIENT


class MemoryContext:
    """Per-invocation wrapper around AgentCore Memory.

    Exception-swallowing by design — memory failures MUST NOT break the
    agent turn. Failures return empty results and log warnings.
    """

    def __init__(self, memory_id: str, actor_id: str, session_id: str,
                 strategies: list[str]):
        self._memory_id = memory_id
        self._actor_id = actor_id
        self._session_id = session_id
        self._strategies = list(strategies or [])

    async def list_preferences(self) -> list[dict]:
        try:
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(
                None,
                lambda: _get_memory_client().list_memory_records(
                    memoryId=self._memory_id,
                    namespace=f"/users/{self._actor_id}/preferences/",
                    maxResults=50,
                ),
            )
            return resp.get("memoryRecordSummaries", [])
        except Exception as e:
            _log.warning("list_preferences failed: %s", e)
            return []

    async def retrieve_summaries(self, query: str, top_k: int = 3) -> list[dict]:
        try:
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(
                None,
                lambda: _get_memory_client().retrieve_memory_records(
                    memoryId=self._memory_id,
                    namespace=f"/users/{self._actor_id}/summaries/",
                    searchCriteria={"searchQuery": query, "topK": top_k},
                ),
            )
            return resp.get("memoryRecordSummaries", [])
        except Exception as e:
            _log.warning("retrieve_summaries failed: %s", e)
            return []

    async def record_user_turn(self, text: str) -> None:
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: _get_memory_client().create_event(
                    memoryId=self._memory_id,
                    actorId=self._actor_id,
                    sessionId=self._session_id,
                    eventTimestamp=int(time.time()),
                    payload=[{
                        "conversational": {
                            "role": "USER",
                            "content": {"text": text},
                        },
                    }],
                ),
            )
        except Exception as e:
            _log.warning("record_user_turn failed: %s", e)

    async def record_assistant_turn(self, text: str) -> None:
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: _get_memory_client().create_event(
                    memoryId=self._memory_id,
                    actorId=self._actor_id,
                    sessionId=self._session_id,
                    eventTimestamp=int(time.time()),
                    payload=[{
                        "conversational": {
                            "role": "ASSISTANT",
                            "content": {"text": text},
                        },
                    }],
                ),
            )
        except Exception as e:
            _log.warning("record_assistant_turn failed: %s", e)

    def make_recall_facts_tool(self):
        ns = f"/users/{self._actor_id}/facts/"
        memory_id = self._memory_id

        @_tool
        def recall_facts(query: str) -> str:
            """Search the user's long-term factual memory.

            Use when the user's question implies they told you something
            factual before that you don't see in this conversation.

            Args:
                query: what you're looking for, in plain English.

            Returns:
                JSON array of matched record content objects.
            """
            try:
                resp = _get_memory_client().retrieve_memory_records(
                    memoryId=memory_id,
                    namespace=ns,
                    searchCriteria={"searchQuery": query, "topK": 5},
                )
                return json.dumps([
                    r.get("content") for r in resp.get("memoryRecordSummaries", [])
                ])
            except Exception as e:
                _log.warning("recall_facts failed: %s", e)
                return "[]"

        return recall_facts

    def make_recall_episodes_tool(self):
        ns = f"/users/{self._actor_id}/episodes/"
        memory_id = self._memory_id

        @_tool
        def recall_episodes(query: str) -> str:
            """Search the user's past scenarios and episodes.

            Use when the user asks about past situations or when you
            need context on a specific past task.

            Args:
                query: what you're looking for.

            Returns:
                JSON array of matched episode content objects.
            """
            try:
                resp = _get_memory_client().retrieve_memory_records(
                    memoryId=memory_id,
                    namespace=ns,
                    searchCriteria={"searchQuery": query, "topK": 3},
                )
                return json.dumps([
                    r.get("content") for r in resp.get("memoryRecordSummaries", [])
                ])
            except Exception as e:
                _log.warning("recall_episodes failed: %s", e)
                return "[]"

        return recall_episodes
