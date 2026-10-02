"""Shared local-client guard for manually triggered operations."""

from __future__ import annotations

import ipaddress
from collections.abc import Sequence

from fastapi import Request


def is_allowed_local_request(request: Request, allowed_cidrs: Sequence[str]) -> bool:
    if "x-forwarded-for" in request.headers or "forwarded" in request.headers:
        return False
    if request.client is None:
        return False
    try:
        address = ipaddress.ip_address(request.client.host)
        return any(address in ipaddress.ip_network(cidr, strict=False) for cidr in allowed_cidrs)
    except ValueError:
        return False
