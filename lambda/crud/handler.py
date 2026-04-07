"""CRUD Lambda handler — main entry point."""
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler import APIGatewayRestResolver
from aws_lambda_powertools.utilities.typing import LambdaContext

from crud.workspaces import router as workspaces_router
from crud.agents import router as agents_router
from crud.skills import router as skills_router
from crud.tools import router as tools_router

logger = Logger(service="agent-studio-crud")
app = APIGatewayRestResolver()

app.include_router(workspaces_router)
app.include_router(agents_router)
app.include_router(skills_router)
app.include_router(tools_router)


@app.get("/api/health")
def health():
    return {"status": "ok"}


def lambda_handler(event: dict, context: LambdaContext) -> dict:
    return app.resolve(event, context)
