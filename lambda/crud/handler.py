from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler import APIGatewayRestResolver
from aws_lambda_powertools.utilities.typing import LambdaContext

logger = Logger(service="agent-studio-crud")
app = APIGatewayRestResolver()

@app.get("/api/health")
def health():
    return {"status": "ok"}

def lambda_handler(event: dict, context: LambdaContext) -> dict:
    return app.resolve(event, context)
