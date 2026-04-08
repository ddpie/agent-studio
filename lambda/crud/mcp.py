"""MCP Gateway discovery endpoints."""
import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler.api_gateway import Router

from shared.config import REGION
from shared.middleware import auth_check
from shared.response import success, internal_error

router = Router()
logger = Logger(child=True)

_control = None


def _get_control():
    global _control
    if _control is None:
        _control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    return _control


@router.get("/api/workspaces/<wsId>/mcp/gateways")
def list_gateways(wsId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err

    try:
        control = _get_control()
        resp = control.list_gateways()
        gateways = [
            {
                "id": gw["gatewayId"],
                "name": gw.get("name", ""),
                "status": gw.get("status", ""),
            }
            for gw in resp.get("gateways", [])
        ]
        return success({"items": gateways})
    except Exception:
        logger.exception("list_gateways failed")
        return internal_error()


@router.get("/api/workspaces/<wsId>/mcp/gateways/<gatewayId>/targets")
def list_targets(wsId: str, gatewayId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err

    try:
        control = _get_control()
        resp = control.list_gateway_targets(gatewayIdentifier=gatewayId)
        targets = [
            {
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "endpointUrl": t.get("endpointUrl", ""),
                "status": t.get("status", ""),
            }
            for t in resp.get("targets", [])
        ]
        return success({"items": targets})
    except Exception:
        logger.exception("list_targets failed for gateway=%s", gatewayId)
        return internal_error()
