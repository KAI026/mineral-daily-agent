from __future__ import annotations

import asyncio
import ipaddress
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import dns.asyncresolver
import dns.exception
import dns.resolver
import httpx


class OutboundRequestError(RuntimeError):
    """An outbound request was refused or failed validation."""


@dataclass(frozen=True, slots=True)
class DownloadedContent:
    content: bytes
    final_url: str
    content_type: str


@dataclass(frozen=True, slots=True)
class NetworkPolicy:
    resolve_dns: bool = True
    allowed_schemes: tuple[str, ...] = ("https",)
    max_redirects: int = 4


DEFAULT_NETWORK_POLICY = NetworkPolicy()


def _is_forbidden_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value)
    return any(
        (
            ip.is_private,
            ip.is_loopback,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_reserved,
            ip.is_unspecified,
        )
    )


async def validate_remote_url(url: str, policy: NetworkPolicy = DEFAULT_NETWORK_POLICY) -> None:
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in policy.allowed_schemes:
        allowed = ", ".join(policy.allowed_schemes)
        raise OutboundRequestError(f"URL scheme must be one of: {allowed}")
    if not parsed.hostname or parsed.username or parsed.password:
        raise OutboundRequestError("URL must contain a public host and no embedded credentials")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"}:
        raise OutboundRequestError("local hosts are not allowed")
    try:
        if _is_forbidden_ip(host):
            raise OutboundRequestError("private or special-purpose IP addresses are not allowed")
        return
    except ValueError:
        pass
    if not policy.resolve_dns:
        return

    async def resolve(record_type: str) -> set[str]:
        try:
            answer = await dns.asyncresolver.resolve(host, record_type, lifetime=5, search=False)
        except dns.resolver.NoAnswer:
            return set()
        except dns.resolver.NXDOMAIN as exc:
            raise OutboundRequestError(f"host could not be resolved: {host}") from exc
        except (dns.exception.Timeout, dns.resolver.NoNameservers) as exc:
            raise OutboundRequestError(f"DNS resolution failed or timed out: {host}") from exc
        return {str(record) for record in answer}

    ipv4, ipv6 = await asyncio.gather(resolve("A"), resolve("AAAA"))
    addresses = ipv4 | ipv6
    if not addresses or any(_is_forbidden_ip(address) for address in addresses):
        raise OutboundRequestError("host resolves to a private or special-purpose address")


async def download_limited(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_bytes: int,
    accepted_content_types: tuple[str, ...] = (),
    policy: NetworkPolicy = DEFAULT_NETWORK_POLICY,
) -> DownloadedContent:
    current = url
    for _ in range(policy.max_redirects + 1):
        await validate_remote_url(current, policy)
        async with client.stream("GET", current, follow_redirects=False) as response:
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise OutboundRequestError("redirect response omitted the Location header")
                current = urljoin(current, location)
                continue
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise OutboundRequestError(
                    f"upstream returned HTTP {response.status_code}"
                ) from exc
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if accepted_content_types and not any(
                content_type == accepted or content_type.startswith(f"{accepted}/")
                for accepted in accepted_content_types
            ):
                expected = ", ".join(accepted_content_types)
                raise OutboundRequestError(
                    f"unexpected content type {content_type or '<missing>'}; expected {expected}"
                )
            length = response.headers.get("content-length")
            if length and length.isdigit() and int(length) > max_bytes:
                raise OutboundRequestError(f"response exceeds the {max_bytes}-byte limit")
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise OutboundRequestError(f"response exceeds the {max_bytes}-byte limit")
                chunks.append(chunk)
            return DownloadedContent(b"".join(chunks), str(response.url), content_type)
    raise OutboundRequestError(f"too many redirects (limit: {policy.max_redirects})")
