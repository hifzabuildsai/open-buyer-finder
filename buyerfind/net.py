"""Bounded HTTP with pinned public DNS for untrusted URLs; no shell or browser."""
from __future__ import annotations

import http.client
import ipaddress
import json
import os
import socket
import ssl
from urllib.parse import urljoin, urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener, getproxies


class FetchError(ValueError):
    pass


class NoProxyRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def proxy_request(method, url, headers, payload, timeout, max_bytes):
    """Explicit trusted egress mode: proxy must enforce public-only DNS/address policy."""
    proxies = {k:v for k,v in getproxies().items() if k in {"http","https"}}
    if not proxies:
        raise FetchError("Trusted proxy mode requires HTTP(S)_PROXY")
    h={"User-Agent":"OpenBuyerFinder/0.2 (+public research)","Accept-Encoding":"identity"}
    h.update(headers or {})
    data=None if payload is None else json.dumps(payload).encode("utf-8")
    if data is not None:
        h["Content-Type"]="application/json"
    try:
        try:
            response=build_opener(ProxyHandler(proxies),NoProxyRedirect).open(Request(url,data=data,headers=h,method=method),timeout=timeout)
        except HTTPError as exc:
            response=exc
        with response:
            return response.status,dict((k.lower(),v) for k,v in response.headers.items()),response.read(max_bytes+1)
    except (OSError,URLError,ValueError):
        raise FetchError(f"{urlsplit(url).hostname}: proxy connection or response failure") from None


def validate_url(url, *, local=False):
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 33 for c in url):
        raise FetchError("Invalid URL")
    p = urlsplit(url)
    if p.scheme not in {"https", "http"} or not p.hostname or p.username or p.password:
        raise FetchError("Use an http(s) URL without credentials")
    try:
        port = p.port or (443 if p.scheme == "https" else 80)
    except ValueError:
        raise FetchError("Invalid URL port") from None
    is_local = p.hostname in {"localhost", "127.0.0.1", "::1"}
    if is_local and local:
        return p, port
    if port not in {80, 443}:
        raise FetchError("Public sources must use port 80 or 443")
    try:
        address = ipaddress.ip_address(p.hostname)
    except ValueError:
        if "." not in p.hostname or p.hostname.endswith((".localhost", ".local", ".internal", ".test", ".invalid")):
            raise FetchError("Public sources require a public hostname") from None
    else:
        if not address.is_global:
            raise FetchError("Private and reserved network addresses are blocked")
    return p, port


def request(method, url, *, headers=None, payload=None, local=False, redirects=0, timeout=20, max_bytes=2_000_000):
    """Return (status, headers, bytes). DNS is resolved once and the IP is pinned.

    local=True only permits literal loopback provider endpoints from configuration,
    never arbitrary private LAN addresses. Authenticated API redirects are disabled.
    """
    p, port = validate_url(url, local=local)
    host = p.hostname.encode("idna").decode("ascii")
    trusted_proxy = os.environ.get("BUYERFIND_TRUST_PROXY") == "1" and p.hostname not in {"localhost","127.0.0.1","::1"}
    if trusted_proxy:
        status,response_headers,data=proxy_request(method,url,headers,payload,timeout,max_bytes)
    try:
        if trusted_proxy:
            addresses = []
        else:
            addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        for address in addresses:
            ip = ipaddress.ip_address(address[4][0])
            if not ip.is_global and not (local and host in {"localhost", "127.0.0.1", "::1"} and ip.is_loopback):
                raise FetchError("DNS resolved to a private or reserved address")
        if not addresses and not trusted_proxy:
            raise FetchError("Hostname did not resolve")
        if not trusted_proxy:
            family, kind, proto, _, sockaddr = addresses[0]
            sock = socket.socket(family, kind, proto)
            sock.settimeout(timeout)
            try:
                sock.connect(sockaddr)
                if p.scheme == "https":
                    sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
                conn = http.client.HTTPConnection(host, port, timeout=timeout)
                conn.sock = sock
                h = {"User-Agent": "OpenBuyerFinder/0.2 (+public research)", "Accept-Encoding": "identity"}
                h.update(headers or {})
                body = None if payload is None else json.dumps(payload).encode("utf-8")
                if body is not None:
                    h["Content-Type"] = "application/json"
                conn.request(method, (p.path or "/") + (("?" + p.query) if p.query else ""), body=body, headers=h)
                response = conn.getresponse()
                status, response_headers = response.status, dict((k.lower(), v) for k, v in response.getheaders())
                data = response.read(max_bytes + 1)
                conn.close()
            finally:
                sock.close()
    except FetchError:
        raise
    except (OSError, http.client.HTTPException, ValueError):
        raise FetchError(f"{host}: connection or response failure") from None
    if len(data) > max_bytes:
        raise FetchError(f"{host}: response exceeds size limit")
    if status in {301, 302, 303, 307, 308} and redirects:
        if method != "GET" or headers:
            raise FetchError("Authenticated redirects are disabled")
        return request("GET", urljoin(url, response_headers.get("location", "")), redirects=redirects-1,
                       timeout=timeout, max_bytes=max_bytes)
    response_headers["_resolved_url"] = url
    return status, response_headers, data


def json_request(method, url, *, headers=None, payload=None, local=False, timeout=45):
    status, _, body = request(method, url, headers=headers, payload=payload, local=local,
                              timeout=timeout, max_bytes=4_000_000)
    if not 200 <= status < 300:
        raise FetchError(f"{urlsplit(url).hostname}: HTTP {status}; check access, quota or settings")
    try:
        result = json.loads(body)
    except (ValueError, UnicodeError):
        raise FetchError("Provider returned invalid JSON") from None
    if not isinstance(result, dict):
        raise FetchError("Provider returned an unexpected JSON shape")
    return result
