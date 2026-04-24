"""Kiro CLI backend for Meta-Agent.

Replaces the Strands `Agent.stream_async()` loop with a Kiro-CLI ACP subprocess.
Existing tools in meta-agent/tools/ are reused via a local HTTP MCP server.
"""
