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
    import socket
    import ipaddress
    from urllib.parse import urlparse

    if not url or not url.startswith(("http://", "https://")):
        return "Error: Invalid URL. Must start with http:// or https://"

    # SSRF protection: reject private/metadata IPs
    try:
        hostname = urlparse(url).hostname
        if hostname:
            _blocked = ["127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "::1/128", "fc00::/7", "fe80::/10"]
            for _fam, _t, _p, _c, sa in socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM):
                ip = ipaddress.ip_address(sa[0])
                if any(ip in ipaddress.ip_network(n) for n in _blocked):
                    return f"Error: URL resolves to blocked IP range: {ip}"
    except Exception as e:
        return f"Error validating URL: {e}"

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
