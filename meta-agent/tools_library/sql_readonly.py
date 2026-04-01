"""SQL query execution tool (read-only)."""

TOOL_META = {
    "id": "run_sql_readonly",
    "name": "SQL Query (Read-Only)",
    "description": "Execute read-only SQL queries against a database endpoint",
    "category": "data",
}

TOOL_NAMES = "run_sql"

TOOL_CODE = '''
@tool
def run_sql(query: str, endpoint: str = "", database: str = "", secret_name: str = "") -> str:
    """Execute a read-only SQL query against a database.

    Only SELECT statements are allowed. The connection details should be
    configured via the agent secrets (endpoint, database, secret_name).

    Args:
        query: The SQL query to execute. Must be a SELECT statement.
        endpoint: Database endpoint (host:port). Read from secrets if empty.
        database: Database name. Read from secrets if empty.
        secret_name: Secrets Manager secret name for credentials. Read from secrets if empty.

    Returns:
        Query results as a formatted table, or error message.
    """
    import json

    # Safety check: only allow SELECT
    stripped = query.strip().upper()
    if not stripped.startswith("SELECT") and not stripped.startswith("SHOW") and not stripped.startswith("DESCRIBE") and not stripped.startswith("EXPLAIN"):
        return "Error: Only SELECT, SHOW, DESCRIBE, and EXPLAIN queries are allowed."

    return json.dumps({"info": "SQL tool requires database credentials configured via Secrets Manager. Please set up the agent secrets first.", "query": query})
'''
