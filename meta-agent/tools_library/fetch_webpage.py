"""Fetch and parse a webpage."""

TOOL_META = {
    "id": "fetch_webpage",
    "name": "Fetch Webpage",
    "description": "Fetch a URL and extract text content",
    "category": "web",
}

TOOL_NAMES = "fetch_webpage"

TOOL_CODE = '''
@tool
def fetch_webpage(url: str, max_length: int = 5000) -> str:
    """Fetch a webpage and return its text content.

    Args:
        url: The URL to fetch.
        max_length: Maximum characters to return. Default 5000.

    Returns:
        The text content of the page, truncated to max_length.
    """
    import urllib.request
    import re

    headers = {"User-Agent": "Mozilla/5.0"}
    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        return f"Error fetching URL: {e}"

    # Remove script and style tags
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL)
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", " ", html)
    # Clean whitespace
    text = re.sub(r"\\s+", " ", text).strip()

    return text[:max_length]
'''
