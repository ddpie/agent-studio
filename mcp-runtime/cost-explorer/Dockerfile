FROM python:3.13-slim

RUN pip install --no-cache-dir mcp-proxy awslabs-cost-explorer-mcp-server

EXPOSE 8000

CMD ["mcp-proxy", "--port", "8000", "--host", "0.0.0.0", "--stateless", "--pass-environment", "--", "awslabs.cost-explorer-mcp-server"]
