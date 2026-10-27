"""
Security module: SSRF prevention, XSS sanitization, safe JSON-in-HTML serialization,
tenant client-credential authentication and JWT handling.
OWASP API Security Top 10 hardened.
"""

from __future__ import annotations
import hmac
import html
import ipaddress
import json
import re
import secrets
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Union
from urllib.parse import urlsplit

import bleach
import jwt
from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.config import settings

IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]

# HTTP Bearer scheme
bearer_scheme = HTTPBearer(auto_error=False)

# Disallowed IP networks for SSRF protection (defense in depth on top of `is_global`)
BLOCKED_IP_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),      # Loopback
    ipaddress.ip_network("10.0.0.0/8"),       # Private Class A
    ipaddress.ip_network("172.16.0.0/12"),    # Private Class B
    ipaddress.ip_network("192.168.0.0/16"),   # Private Class C
    ipaddress.ip_network("169.254.0.0/16"),   # Link-local / Cloud Metadata (AWS/GCP/Azure)
    ipaddress.ip_network("100.64.0.0/10"),    # Carrier-grade NAT (RFC 6598)
    ipaddress.ip_network("0.0.0.0/8"),        # "This host on this network"
    ipaddress.ip_network("192.0.0.0/24"),     # IETF protocol assignments
    ipaddress.ip_network("198.18.0.0/15"),    # Benchmarking
    ipaddress.ip_network("224.0.0.0/4"),      # Multicast
    ipaddress.ip_network("240.0.0.0/4"),      # Reserved + broadcast
    ipaddress.ip_network("::/128"),           # IPv6 unspecified
    ipaddress.ip_network("::1/128"),          # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),         # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),        # IPv6 link-local
    ipaddress.ip_network("fd00:ec2::254/128"),  # AWS IMDS over IPv6
]

# NAT64 well-known prefix embeds an IPv4 address in the low 32 bits
NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")

# Hostnames that must never be fetched even before DNS resolution
BLOCKED_HOSTNAMES = ("localhost", "metadata.google.internal", "metadata", "instance-data")
BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain", ".home.arpa")

# Characters that are never legal unencoded in a URL (RFC 3986) and enable injection
_URL_FORBIDDEN_CHARS = re.compile(r"[\x00-\x20\x7f\\<>\"'`{}|^]")


@dataclass(frozen=True)
class ValidatedUrl:
    """A URL that passed SSRF validation, plus the IPs it resolved to (for DNS pinning)."""
    url: str
    hostname: str
    resolved_ips: tuple[str, ...]


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def _embedded_ipv4(ip: IPAddress) -> Optional[ipaddress.IPv4Address]:
    """Extracts IPv4 addresses tunnelled inside IPv6 (mapped, 6to4, Teredo, NAT64)."""
    if not isinstance(ip, ipaddress.IPv6Address):
        return None
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip.sixtofour is not None:
        return ip.sixtofour
    if ip.teredo is not None:
        return ip.teredo[1]
    if ip in NAT64_PREFIX:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def ip_restriction_reason(ip: IPAddress) -> Optional[str]:
    """Returns why an IP is not a safe outbound destination, or None if it is publicly routable."""
    embedded = _embedded_ipv4(ip)
    if embedded is not None:
        inner = ip_restriction_reason(embedded)
        if inner:
            return f"embedded IPv4 {embedded}: {inner}"
    for network in BLOCKED_IP_NETWORKS:
        if ip in network:
            return f"disallowed network {network}"
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        return "restricted IP"
    if ip.is_unspecified or not ip.is_global:
        return "non-globally-routable IP"
    return None


def resolve_and_validate_url(raw_url: str) -> ValidatedUrl:
    """
    Strictly validates a URL against SSRF threats and returns the resolved IPs.
    Enforces:
    1. Scheme MUST be https:// and port MUST be in ALLOWED_PORTS (default 443).
    2. No embedded credentials (userinfo), no control/forbidden characters, bounded length.
    3. Hostname must not be an internal name and must resolve ONLY to globally routable IPs
       (blocks loopback, RFC1918, CGNAT, link-local/cloud metadata 169.254.169.254, IPv6 ULA,
       and IPv4 addresses smuggled through IPv4-mapped / 6to4 / Teredo / NAT64 IPv6).
    Callers that perform the actual fetch should connect to `resolved_ips` (DNS pinning)
    to defeat DNS-rebinding between validation and use.
    """
    if not raw_url or not isinstance(raw_url, str):
        raise _bad_request("Invalid URL provided: URL string cannot be empty.")

    clean_url = raw_url.strip()
    if len(clean_url) > settings.MAX_URL_LENGTH:
        raise _bad_request(f"URL exceeds maximum length of {settings.MAX_URL_LENGTH} characters.")

    try:
        parsed = urlsplit(clean_url)
        port = parsed.port
    except ValueError:
        raise _bad_request("Malformed URL or invalid port.")

    # 1. Scheme check: only HTTPS allowed
    scheme = parsed.scheme.lower()
    if scheme not in settings.ALLOWED_SCHEMES:
        raise _bad_request(
            f"Insecure URL scheme '{scheme or '(none)'}'. Only HTTPS is permitted to mitigate SSRF."
        )

    if _URL_FORBIDDEN_CHARS.search(clean_url):
        raise _bad_request("URL contains forbidden characters (whitespace, quotes, angle brackets or control characters).")

    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise _bad_request("URLs with embedded credentials (userinfo) are not permitted.")

    hostname = parsed.hostname
    if not hostname:
        raise _bad_request("URL is missing a valid hostname.")
    hostname = hostname.rstrip(".").lower()

    if port is not None and port not in settings.ALLOWED_PORTS:
        raise _bad_request(f"Port {port} is not permitted. Allowed ports: {list(settings.ALLOWED_PORTS)}.")

    try:
        ascii_host = hostname.encode("idna").decode("ascii")
    except UnicodeError:
        raise _bad_request("Hostname is not a valid internationalized domain name.")

    if ascii_host in BLOCKED_HOSTNAMES or ascii_host.endswith(BLOCKED_HOST_SUFFIXES):
        raise _bad_request(f"SSRF violation: Hostname '{ascii_host}' refers to an internal service.")

    # Check for direct IP literal or resolve hostname
    try:
        try:
            resolved: list[IPAddress] = [ipaddress.ip_address(ascii_host.strip("[]"))]
        except ValueError:
            addr_info = socket.getaddrinfo(ascii_host, port or 443, proto=socket.IPPROTO_TCP)
            resolved = [ipaddress.ip_address(addr[4][0].split("%")[0]) for addr in addr_info]
    except (socket.gaierror, UnicodeError, OSError):
        raise _bad_request(f"Unable to resolve hostname '{ascii_host}'. Host may not exist.")

    if not resolved:
        raise _bad_request(f"DNS resolution returned no IP addresses for hostname '{ascii_host}'.")

    # Every resolved address must be safe: one private A/AAAA record is enough to block.
    for ip in resolved:
        reason = ip_restriction_reason(ip)
        if reason:
            raise _bad_request(
                f"SSRF violation: Hostname '{ascii_host}' resolves to {reason} ({ip})."
            )

    netloc = ascii_host if ":" not in ascii_host else f"[{ascii_host}]"
    if port is not None and port != 443:
        netloc = f"{netloc}:{port}"
    normalized = f"https://{netloc}{parsed.path or ''}"
    if parsed.query:
        normalized += f"?{parsed.query}"
    if parsed.fragment:
        normalized += f"#{parsed.fragment}"

    unique_ips = tuple(dict.fromkeys(str(ip) for ip in resolved))
    return ValidatedUrl(url=normalized, hostname=ascii_host, resolved_ips=unique_ips)


def validate_and_sanitize_url(raw_url: str) -> str:
    """Validates a URL against SSRF threats and returns its normalized, injection-safe form."""
    return resolve_and_validate_url(raw_url).url


# --- XSS / injection sanitization ---

_DANGEROUS_BLOCK_RE = re.compile(
    r"<\s*(script|style|iframe|object|embed|template|noscript|svg|math)\b[^>]*>.*?<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
# C0 controls (except \t \n \r), DEL, zero-width and bidi override characters ("Trojan Source")
_INVISIBLE_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2060\u2066-\u2069\ufeff]")


def sanitize_text(text: str) -> str:
    """
    Reduces untrusted input to inert plain text.
    - Decodes nested HTML entities (defeats double-encoding bypasses).
    - Removes script/style/iframe/svg blocks INCLUDING their content, then strips every tag.
    - Removes residual angle brackets, control characters and bidi overrides.
    The result is plain text; context-specific encoding (HTML, JSON-in-script) happens at output.
    """
    if not isinstance(text, str):
        return text
    value = text
    for _ in range(4):
        previous = value
        value = html.unescape(value)
        value = _DANGEROUS_BLOCK_RE.sub("", value)
        value = bleach.clean(value, tags=[], attributes={}, strip=True, strip_comments=True)
        value = html.unescape(value)
        if value == previous:
            break
    value = value.replace("<", "").replace(">", "")
    value = _INVISIBLE_RE.sub("", value)
    return value.strip()


_JSON_UNSAFE_RE = re.compile(r"[\u2028\u2029\u200b-\u200f\u202a-\u202e\u2060\u2066-\u2069\ufeff]")


def safe_json_for_script(obj: Any, indent: Optional[int] = 2) -> str:
    """
    Serializes JSON so it can be embedded inside <script type="application/ld+json"> safely.
    Escapes '<', '>', '&', U+2028/U+2029 and bidi / zero-width characters as unicode escapes
    (OWASP XSS Prevention, rule 3.1),
    which makes '</script>' break-outs and HTML comment tricks impossible while staying valid JSON.
    """
    raw = json.dumps(obj, indent=indent, ensure_ascii=False)
    escaped = raw.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    # Line separators break JS string literals; bidi / zero-width characters enable Trojan Source tricks.
    return _JSON_UNSAFE_RE.sub(lambda m: f"\\u{ord(m.group(0)):04x}", escaped)


# --- Tenant authentication & JWT ---

def authenticate_tenant_client(tenant_id: str, client_secret: str) -> bool:
    """Constant-time verification of a tenant's client secret (no tenant enumeration oracle)."""
    expected = settings.TENANT_CLIENT_SECRETS.get(tenant_id)
    if expected is None:
        # Compare against a random value so unknown tenants take the same code path.
        hmac.compare_digest(client_secret.encode("utf-8"), secrets.token_bytes(32))
        return False
    return hmac.compare_digest(client_secret.encode("utf-8"), expected.encode("utf-8"))


def create_jwt_token(
    tenant_id: str,
    subject: str = "marketing-analyst",
    role: str = "analyst",
    expires_delta: Optional[timedelta] = None
) -> str:
    """Create a signed JWT token with tenant isolation claims."""
    if role not in settings.ALLOWED_ROLES:
        raise ValueError(f"Role '{role}' cannot be issued. Allowed roles: {settings.ALLOWED_ROLES}.")
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=settings.JWT_EXPIRATION_MINUTES))
    payload = {
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "sub": subject,
        "tenant_id": tenant_id,
        "role": role,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_jwt_token(token: str) -> dict[str, Any]:
    """Decode and verify JWT signature, algorithm, issuer, audience, expiry and required claims."""
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],  # pinned: rejects 'none' and algorithm confusion
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
            leeway=10,
            options={"require": ["exp", "iat", "nbf", "iss", "aud", "sub", "tenant_id", "role"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired. Please authenticate again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if payload.get("role") not in settings.ALLOWED_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Token role is not permitted.")
    return payload


async def get_current_tenant(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme)
) -> dict[str, Any]:
    """FastAPI dependency to extract tenant info from Authorization header."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing. Bearer token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return decode_jwt_token(credentials.credentials)


def verify_tenant_isolation(token_tenant_id: str, resource_tenant_id: str) -> None:
    """BOLA mitigation: Ensure tenant cannot read/write another tenant's records."""
    if not hmac.compare_digest(token_tenant_id.encode("utf-8"), resource_tenant_id.encode("utf-8")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="BOLA Access Denied: the authenticated tenant is not authorized for the requested tenant resource."
        )
