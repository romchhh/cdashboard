"""
Потокове завантаження великих XML-фідів на диск (без утримання всього тіла в RAM).
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

import aiohttp

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


async def stream_url_to_tempfile(
    url: str,
    *,
    suffix: str = ".xml",
    timeout_total: float = 900.0,
    connect_timeout: float = 30.0,
    sock_read_timeout: float = 180.0,
    chunk_size: int = 256 * 1024,
    extra_headers: dict[str, str] | None = None,
) -> Path:
    """
    Завантажує URL у тимчасовий файл чанками.
    aiohttp сам розпаковує gzip/deflate — на диск пишуться вже розпаковані байти.
    Викликайте path.unlink(missing_ok=True) після використання.
    """
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "application/xml,text/xml,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    }
    if extra_headers:
        headers.update(extra_headers)

    timeout = aiohttp.ClientTimeout(
        total=timeout_total,
        connect=connect_timeout,
        sock_read=sock_read_timeout,
    )

    fd, raw_path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    path = Path(raw_path)

    try:
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            async with session.get(url) as resp:
                resp.raise_for_status()
                total = 0
                with open(path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(chunk_size):
                        if not chunk:
                            break
                        f.write(chunk)
                        total += len(chunk)
        logger.info(
            "Фід завантажено у файл: %s (%.1f MB)",
            path.name,
            total / (1024 * 1024),
        )
        return path
    except Exception:
        path.unlink(missing_ok=True)
        raise
