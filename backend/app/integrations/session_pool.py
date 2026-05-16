import aiohttp
import logging
from typing import Any

logger = logging.getLogger(__name__)

_UPSTREAM_TIMEOUT = aiohttp.ClientTimeout(
    total=600,
    connect=30,
    sock_connect=30,
    sock_read=600,
)
_PROBE_TIMEOUT = aiohttp.ClientTimeout(
    total=10,
    connect=5,
    sock_connect=5,
    sock_read=10,
)

TIMEOUT_UPSTREAM = "upstream"
TIMEOUT_PROBE = "probe"

_TIMEOUTS = {
    TIMEOUT_UPSTREAM: _UPSTREAM_TIMEOUT,
    TIMEOUT_PROBE: _PROBE_TIMEOUT,
}


def _build_socks5_connector(socks5_proxy: str | None):
    proxy_url = str(socks5_proxy or "").strip()
    if not proxy_url:
        return None
    try:
        from aiohttp_socks import ProxyConnector
    except ImportError as e:
        raise RuntimeError(
            "SOCKS5 proxy support requires aiohttp-socks. "
            "Install backend requirements and restart the server."
        ) from e
    return ProxyConnector.from_url(proxy_url)


class SessionPool:
    """Reusable aiohttp session pool keyed by (timeout_kind, socks5_proxy)."""

    def __init__(self) -> None:
        self._sessions: dict[tuple[str, str], aiohttp.ClientSession] = {}

    def get(
        self,
        timeout_kind: str = TIMEOUT_UPSTREAM,
        socks5_proxy: str | None = None,
    ) -> aiohttp.ClientSession:
        proxy_key = (socks5_proxy or "").strip()
        key = (timeout_kind, proxy_key)
        session = self._sessions.get(key)
        if session is not None and not session.closed:
            return session
        timeout = _TIMEOUTS.get(timeout_kind, _UPSTREAM_TIMEOUT)
        connector = _build_socks5_connector(proxy_key or None)
        session = aiohttp.ClientSession(timeout=timeout, connector=connector)
        self._sessions[key] = session
        return session

    async def close_all(self) -> None:
        sessions = list(self._sessions.values())
        self._sessions.clear()
        for session in sessions:
            if not session.closed:
                await session.close()
        logger.info("Closed %d pooled HTTP session(s)", len(sessions))


_pool: SessionPool | None = None


def get_pool() -> SessionPool:
    global _pool
    if _pool is None:
        _pool = SessionPool()
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close_all()
        _pool = None
