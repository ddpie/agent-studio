"""URL validation to prevent SSRF attacks against private/metadata IPs."""

import ipaddress
import socket
import urllib.request
from urllib.parse import urlparse

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("255.255.255.255/32"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("ff00::/8"),
]


def validate_url(url: str) -> str:
    """Resolve URL hostname and reject private/loopback/link-local/CGNAT/multicast IPs.

    Returns the validated URL unchanged on success.
    Raises ValueError if the URL targets a blocked IP range.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"URL scheme must be http or https, got: {parsed.scheme!r}")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL has no hostname")

    try:
        addrs = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"DNS resolution failed for {hostname}: {exc}") from exc

    for _family, _type, _proto, _canon, sockaddr in addrs:
        ip = ipaddress.ip_address(sockaddr[0])
        for net in _BLOCKED_NETWORKS:
            if ip in net:
                raise ValueError(f"URL resolves to blocked IP range: {ip}")

    return url


class _ValidatingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Revalidate the redirect target against validate_url before following."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_ValidatingRedirectHandler)


def safe_urlopen(url_or_req, timeout: float = 30, **kwargs):
    """urlopen wrapper that validates the initial URL and every redirect target.

    Accepts either a URL string or a urllib.request.Request. Raises ValueError
    if the URL or any redirect resolves to a blocked IP range.
    """
    if isinstance(url_or_req, urllib.request.Request):
        validate_url(url_or_req.full_url)
    else:
        validate_url(url_or_req)
    return _opener.open(url_or_req, timeout=timeout, **kwargs)
