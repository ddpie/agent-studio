"""Web search tool using DuckDuckGo."""

TOOL_META = {
    "id": "web_search",
    "name": "Web Search",
    "description": "Search the web via DuckDuckGo and return results",
    "category": "web",
}

TOOL_NAMES = "web_search"

TOOL_CODE = '''
@tool
def web_search(query: str, max_results: int = 5) -> str:
    """Search the web using DuckDuckGo and return results.

    Args:
        query: The search query string.
        max_results: Maximum number of results to return. Default 5.

    Returns:
        JSON array of search results with title, url, and snippet.
    """
    import json
    import urllib.request
    import urllib.parse

    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
    headers = {"User-Agent": "Mozilla/5.0"}
    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8")
    except Exception as e:
        return json.dumps({"error": str(e)})

    import re
    results = []
    for match in re.finditer(r'<a rel="nofollow" class="result__a" href="(.*?)">(.*?)</a>', html):
        href, title = match.groups()
        title = re.sub(r"<.*?>", "", title).strip()
        if href and title and len(results) < max_results:
            results.append({"title": title, "url": href})

    return json.dumps(results, ensure_ascii=False, indent=2)
'''
