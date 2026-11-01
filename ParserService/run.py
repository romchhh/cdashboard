#!/usr/bin/env python3
"""Запуск scrape-worker: python run.py

Один процес (workers=1) — job-и в памʼяті. Довгий scrape лише у фонових thread,
HTTP завжди швидкий (202), щоб nginx не давав 502.
"""

from __future__ import annotations

import uvicorn

from app import config


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host=config.HOST,
        port=config.PORT,
        reload=False,
        workers=1,
        log_level="info",
        timeout_keep_alive=5,
        timeout_graceful_shutdown=15,
        # Health/poll мають проходити навіть під scrape.
        limit_concurrency=64,
    )


if __name__ == "__main__":
    main()
