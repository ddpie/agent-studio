"""CRUD Lambda handler — main entry point."""
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler import APIGatewayRestResolver
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

logger = Logger(service="agent-studio-crud")

cors_config = CORSConfig(
    allow_origin="*",
    allow_headers=["Authorization", "Content-Type"],
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


def lambda_handler(event: dict, context: LambdaContext) -> dict:
    return app.resolve(event, context)
