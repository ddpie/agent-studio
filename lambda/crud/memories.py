"""End-user memory management endpoints (GET; DELETE added in later tasks)."""
import asyncio

import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler.api_gateway import Router

from shared.config import WORKSPACES_TABLE, REGION
from shared.memory_actor import build_actor_id
from shared.memory_strategies import STRATEGY_NAMESPACE_PREFIX
from shared.middleware import auth_check
from shared.response import success, bad_request, not_found

router = Router()
logger = Logger(child=True)

_data = None
_workspaces_table = None


def _get_data():
    global _data
    if _data is None:
        _data = boto3.client("bedrock-agentcore", region_name=REGION)
    return _data


def _get_workspaces_table():
    global _workspaces_table
    if _workspaces_table is None:
        _workspaces_table = boto3.resource("dynamodb", region_name=REGION).Table(WORKSPACES_TABLE)
    return _workspaces_table


def _get_workspace_memory_id(workspace_id: str) -> str | None:
    resp = _get_workspaces_table().get_item(
        Key={"workspaceId": workspace_id, "sk": "META"}, ConsistentRead=False)
    item = resp.get("Item")
    if not item:
        return None
    return item.get("memory_id") or None


_STRATEGY_PLURAL_TO_KEY = {
    "preferences": "userPreference",
    "facts":       "semantic",
    "summaries":   "summary",
    "episodes":    "episodic",
}
_INITIAL_PAGE_SIZE = 20
_MAX_PAGE_SIZE = 100


def _namespace_for(strategy_plural: str, actor_id: str) -> str:
    key = _STRATEGY_PLURAL_TO_KEY.get(strategy_plural)
    if key is None:
        raise ValueError(f"unknown strategy: {strategy_plural}")
    return STRATEGY_NAMESPACE_PREFIX[key].replace("{actor_id}", actor_id)


def _list_one_section(memory_id: str, namespace: str, max_results: int,
                      next_token: str | None) -> dict:
    kwargs = {"memoryId": memory_id, "namespace": namespace, "maxResults": max_results}
    if next_token:
        kwargs["nextToken"] = next_token
    try:
        resp = _get_data().list_memory_records(**kwargs)
    except Exception as e:
        logger.warning("list_memory_records failed ns=%s: %s", namespace, e)
        return {"records": [], "nextToken": None}
    records = [{
        "id": r.get("memoryRecordId"),
        "content": r.get("content"),
        "createdAt": r.get("createdAt"),
        "namespace": r.get("namespace"),
    } for r in resp.get("memoryRecordSummaries", [])]
    return {"records": records, "nextToken": resp.get("nextToken") or None}


def _list_my_memories_impl(*, workspace_id: str, agent_id: str, caller_id: str,
                           strategy: str | None, next_token: str | None,
                           max_results: int = _INITIAL_PAGE_SIZE) -> dict:
    memory_id = _get_workspace_memory_id(workspace_id)
    if not memory_id:
        return {}
    actor_id = build_actor_id(agent_id, caller_id)

    if strategy:
        if strategy not in _STRATEGY_PLURAL_TO_KEY:
            raise ValueError(f"unknown strategy: {strategy}")
        ns = _namespace_for(strategy, actor_id)
        return _list_one_section(memory_id, ns, max_results, next_token)

    loop = asyncio.new_event_loop()
    try:
        async def _fetch_all():
            tasks = []
            for s in ("preferences", "facts", "summaries", "episodes"):
                ns = _namespace_for(s, actor_id)
                tasks.append(loop.run_in_executor(
                    None, _list_one_section, memory_id, ns, _INITIAL_PAGE_SIZE, None))
            return await asyncio.gather(*tasks)
        prefs, facts, sums, eps = loop.run_until_complete(_fetch_all())
    finally:
        loop.close()

    return {"preferences": prefs, "facts": facts, "summaries": sums, "episodes": eps}


@router.get("/api/workspaces/<wsId>/agents/<agentId>/my-memories")
def list_my_memories(wsId: str, agentId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="viewer", ws_id=wsId)
    if err:
        return err

    qs = router.current_event.query_string_parameters or {}
    strategy = qs.get("strategy")
    next_token = qs.get("nextToken")
    max_results_raw = qs.get("maxResults")

    if strategy and strategy not in _STRATEGY_PLURAL_TO_KEY:
        return bad_request(f"unknown strategy: {strategy}")

    max_results = _INITIAL_PAGE_SIZE
    if strategy and max_results_raw:
        try:
            max_results = min(int(max_results_raw), _MAX_PAGE_SIZE)
        except ValueError:
            return bad_request("maxResults must be an integer")

    try:
        result = _list_my_memories_impl(
            workspace_id=wsId, agent_id=agentId, caller_id=user_id,
            strategy=strategy, next_token=next_token, max_results=max_results)
    except ValueError as e:
        return bad_request(str(e))

    if not result:
        return not_found()
    return success(result)
