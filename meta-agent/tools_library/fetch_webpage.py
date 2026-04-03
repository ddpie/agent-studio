"""Fetch and parse a webpage."""

TOOL_META = {
    "id": "fetch_webpage",
    "name": "Fetch Webpage",
    "description": "Fetch a URL and extract text content as markdown",
    "category": "web",
}

TOOL_NAMES = "fetch_webpage"

TOOL_CODE = '''
@tool
def fetch_webpage(url: str, max_length: int = 8000) -> str:
    """Fetch a webpage and return its content as clean markdown.

    Args:
        url: The URL to fetch.
        max_length: Maximum characters to return. Default 8000.

    Returns:
        The page content converted to markdown, truncated to max_length.

    Example:
        fetch_webpage("https://docs.aws.amazon.com/bedrock/latest/userguide/what-is-bedrock.html")
    """
    import urllib.request

    if not url or not url.startswith(("http://", "https://")):
        return "Error: Invalid URL. Must start with http:// or https://"

    headers = {"User-Agent": "Mozilla/5.0 (compatible; AgentStudio/1.0)"}
    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        return f"Error fetching URL: {e}"

    # Try markdownify first (better output)
    try:
        from markdownify import markdownify as md
        text = md(html, heading_style="ATX", strip=["script", "style", "nav", "footer"])
    except ImportError:
        # Fallback: regex-based cleanup
        import re
        html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
        html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL)
        html = re.sub(r"<nav[^>]*>.*?</nav>", "", html, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", html)

    # Clean excessive whitespace
    import re
    text = re.sub(r"\\n{3,}", "\\n\\n", text)
    text = re.sub(r" {2,}", " ", text).strip()

    if len(text) > max_length:
        text = text[:max_length] + f"\\n\\n... (truncated at {max_length} chars)"

    return text
'''
