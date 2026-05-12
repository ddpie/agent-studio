"""Knowledge Base retrieval tool — injected into agent zip at deploy time."""

TOOL_META = {
    "id": "kb_retrieve",
    "name": "Knowledge Base Retrieval",
    "description": "Search bound knowledge bases using semantic retrieval",
    "category": "knowledge",
}

TOOL_NAMES = "kb_retrieve"

TOOL_CODE = '''
@tool
def kb_retrieve(query: str, kb_name: str = "", top_k: int = 5) -> str:
    """Search bound knowledge bases for relevant information.

    Args:
        query: Natural language search query.
        kb_name: Optional — name of a specific KB to search. If empty, searches all bound KBs.
        top_k: Number of results per KB. Default 5.

    Returns:
        JSON with retrieval results sorted by relevance score.
    """
    import json
    import boto3

    if not BOUND_KBS:
        return json.dumps({"error": "no_kb_bound", "message": "This agent has no knowledge base attached."})

    targets = BOUND_KBS
    if kb_name:
        targets = [kb for kb in BOUND_KBS if kb["name"] == kb_name]
        if not targets:
            available = [kb["name"] for kb in BOUND_KBS]
            return json.dumps({"error": "kb_not_found", "available": available})

    client = boto3.client("bedrock-agent-runtime", region_name=REGION)
    all_results = []

    for kb in targets:
        try:
            resp = client.retrieve(
                knowledgeBaseId=kb["bedrock_kb_id"],
                retrievalQuery={"text": query},
                retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": top_k}},
            )
            for r in resp.get("retrievalResults", []):
                all_results.append({
                    "kb_name": kb["name"],
                    "content": r["content"]["text"],
                    "score": r.get("score", 0.0),
                    "source_uri": r.get("location", {}).get("s3Location", {}).get("uri", ""),
                })
        except Exception as e:
            all_results.append({"kb_name": kb["name"], "error": str(e)})

    all_results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return json.dumps({"results": all_results[:top_k * len(targets)]}, ensure_ascii=False, indent=2)
'''
