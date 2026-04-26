"""CRUD Lambda handler — main entry point."""
import os

from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler import APIGatewayRestResolver, Response
from aws_lambda_powertools.event_handler.api_gateway import CORSConfig
from aws_lambda_powertools.utilities.typing import LambdaContext

from crud.workspaces import router as workspaces_router
from crud.agents import router as agents_router
from crud.skills import router as skills_router
from crud.tools import router as tools_router
from crud.uploads import router as uploads_router
from crud.secrets import router as secrets_router
from crud.mcp import router as mcp_router
from crud.runtime import router as runtime_router

from crud.traces import router as traces_router
from crud.meta_agent import router as meta_agent_router
from crud.a2a_keys import router as a2a_keys_router
from crud.schedules import router as schedules_router
from crud.costs import router as costs_router
from crud.logs import router as logs_router
from crud.memories import router as memories_router
from crud.runs import router as runs_router
from crud.kiro_key import router as kiro_key_router
from crud.chat import router as chat_router
from crud.workspace_iam import router as workspace_iam_router

logger = Logger(service="agent-studio-crud")

cors_config = CORSConfig(
    allow_origin="*",
    allow_headers=["Authorization", "Content-Type", "x-workspace-id"],
    max_age=3600,
)
app = APIGatewayRestResolver(cors=cors_config)

app.include_router(workspaces_router)
app.include_router(agents_router)
app.include_router(skills_router)
app.include_router(tools_router)
app.include_router(uploads_router)
app.include_router(secrets_router)
app.include_router(mcp_router)
app.include_router(runtime_router)

app.include_router(traces_router)
app.include_router(meta_agent_router)
app.include_router(a2a_keys_router)
app.include_router(schedules_router)
app.include_router(costs_router)
app.include_router(logs_router)
app.include_router(memories_router)
app.include_router(runs_router)
app.include_router(kiro_key_router)
app.include_router(chat_router)
app.include_router(workspace_iam_router)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.exception_handler(Exception)
def handle_unhandled(ex: Exception):
    logger.exception("Unhandled exception")
    from aws_lambda_powertools.event_handler import Response
    return Response(
        status_code=500,
        content_type="application/json",
        body='{"error":"Internal server error","code":"INTERNAL_ERROR"}',
    )


ORIGIN_VERIFY_HEADER = "x-origin-verify"
ORIGIN_VERIFY_VALUE = os.environ.get("ORIGIN_VERIFY_VALUE", "")


def _origin_verify_ok(event: dict) -> bool:
    """Reject requests that didn't come through CloudFront.

    CloudFront injects `x-origin-verify: <secret>` on the /api/* origin.
    Direct APIGW URL callers don't have the secret; this closes the
    WAF-bypass path where a valid Cognito JWT is otherwise sufficient.
    """
    if not ORIGIN_VERIFY_VALUE:
        return True
    headers = event.get("headers") or {}
    received = None
    for k, v in headers.items():
        if k.lower() == ORIGIN_VERIFY_HEADER:
            received = v
            break
    return received == ORIGIN_VERIFY_VALUE


def lambda_handler(event: dict, context: LambdaContext) -> dict:
    if not _origin_verify_ok(event):
        logger.warning("origin-verify missing/invalid — rejecting direct APIGW hit")
        return {
            "statusCode": 403,
            "headers": {"content-type": "application/json"},
            "body": '{"error":"Forbidden"}',
        }
    return app.resolve(event, context)
