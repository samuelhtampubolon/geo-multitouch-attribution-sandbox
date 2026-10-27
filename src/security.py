"""
Security module: SSRF prevention, XSS sanitization, and JWT authentication.
OWASP API Top 10 Security Hardened.
"""

from __future__ import annotations
import html
import ipaddress
import socket
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlparse

import bleach
import jwt
from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.config import settings

# HTTP Bearer scheme
bearer_scheme = HTTPBearer(auto_error=False)

# Disallowed IP networks for SSRF protection
BLOCKED_IP_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),      # Loopback
    ipaddress.ip_network("10.0.0.0/8"),       # Private Class A
    ipaddress.ip_network("172.16.0.0/12"),    # Private Class B
    ipaddress.ip_network("192.168.0.0/16"),   # Private Class C
    ipaddress.ip_network("169.254.0.0/16"),   # Link-local / Cloud Metadata (AWS/GCP/Azure)
    ipaddress.ip_network("0.0.0.0/8"),        # "This host on this network"
    ipaddress.ip_network("224.0.0.0/4"),      # Multicast
    ipaddress.ip_network("240.0.0.0/4"),      # Reserved
    ipaddress.ip_network("::1/128"),          # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),         # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),        # IPv6 link-local
]


def validate_and_sanitize_url(raw_url: str) -> str:
    """
    Strictly validate a URL against SSRF threats.
    Enforces:
    1. Scheme MUST be https://
    2. Hostname must resolve to a valid public IP address (not loopback, private, or metadata 169.254.169.254)
    """
    if not raw_url or not isinstance(raw_url, str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid URL provided: URL string cannot be empty."
        )

    clean_url = raw_url.strip()
    parsed = urlparse(clean_url)

    # 1. Scheme check: only HTTPS allowed
    if parsed.scheme.lower() not in settings.ALLOWED_SCHEMES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insecure URL scheme '{parsed.scheme}'. Only HTTPS is permitted to mitigate SSRF."
        )

    hostname = parsed.hostname
    if not hostname:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="URL is missing a valid hostname."
        )

    # Check for direct IP string or resolve hostname
    try:
        # Check if hostname itself is directly an IP literal
        try:
            ip_obj = ipaddress.ip_address(hostname)
            resolved_ips = [ip_obj]
        except ValueError:
            # It is a domain name, resolve via DNS
            addr_info = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
            resolved_ips = [ipaddress.ip_address(addr[4][0]) for addr in addr_info]

        if not resolved_ips:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"DNS resolution returned no IP addresses for hostname '{hostname}'."
            )

        for ip in resolved_ips:
            # Check standard private / loopback attributes
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"SSRF violation: Hostname '{hostname}' resolves to restricted IP {ip}."
                )

            # Check explicit blocked networks list
            for network in BLOCKED_IP_NETWORKS:
                if ip in network:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"SSRF violation: Hostname '{hostname}' resolves to disallowed network {network}."
                    )
    except socket.gaierror:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unable to resolve hostname '{hostname}'. Host may not exist."
        )

    # Return safe escaped URL
    return bleach.clean(clean_url)


def sanitize_text(text: str) -> str:
    """Sanitize strings against XSS and template injection."""
    if not isinstance(text, str):
        return text
    # Escape HTML special chars and clean dangerous tags
    escaped = html.escape(text.strip())
    return bleach.clean(escaped, tags=[], strip=True)


def create_jwt_token(
    tenant_id: str,
    subject: str = "marketing-analyst",
    role: str = "analyst",
    expires_delta: Optional[timedelta] = None
) -> str:
    """Create a signed JWT token with tenant isolation claims."""
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=settings.JWT_EXPIRATION_MINUTES))
    payload = {
        "sub": subject,
        "tenant_id": tenant_id,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp())
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_jwt_token(token: str) -> dict[str, Any]:
    """Decode and verify JWT signature and expiration."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired. Please authenticate again."
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token."
        )


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
    token = credentials.credentials
    payload = decode_jwt_token(token)
    if "tenant_id" not in payload:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token missing required tenant_id claim."
        )
    return payload


def verify_tenant_isolation(token_tenant_id: str, resource_tenant_id: str) -> None:
    """BOLA mitigation: Ensure tenant cannot read/write another tenant's records."""
    if token_tenant_id != resource_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"BOLA Access Denied: Tenant '{token_tenant_id}' cannot access resources belonging to '{resource_tenant_id}'."
        )
