"""Підключає cosmyparser з монорепо або з runtime/ після pack.sh."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from . import config

logger = logging.getLogger(__name__)

_READY = False
_ENGINE_ERROR: str | None = None
_ROOT: Path | None = None


def _candidate_roots() -> list[Path]:
    service = Path(__file__).resolve().parent.parent
    roots: list[Path] = []
    for raw in (
        os.getenv("PARSER_PROJECT_ROOT", "").strip(),
        str(service / "runtime"),
        str(config.PROJECT_ROOT),
        str(service.parent),
    ):
        if not raw:
            continue
        p = Path(raw).expanduser().resolve()
        if p not in roots:
            roots.append(p)
    return roots


def find_project_root() -> Path | None:
    for root in _candidate_roots():
        if (root / "cosmyparser" / "cosmy_parse.py").is_file():
            return root
    return None


def setup() -> Path:
    """Додає шляхи до sys.path і перевіряє імпорт двигуна."""
    global _READY, _ENGINE_ERROR, _ROOT

    root = find_project_root() or config.PROJECT_ROOT.resolve()
    _ROOT = root

    for path in (root, root / "cosmyparser"):
        s = str(path)
        if path.is_dir() and s not in sys.path:
            sys.path.insert(0, s)

    config.WORK_DIR.mkdir(parents=True, exist_ok=True)

    try:
        import cosmy_parse  # noqa: F401
        import catalog_report  # noqa: F401

        _READY = True
        _ENGINE_ERROR = None
        logger.info("Engine OK root=%s cosmy_parse=%s", root, getattr(cosmy_parse, "__file__", "?"))
    except Exception as exc:
        _READY = False
        _ENGINE_ERROR = str(exc)
        logger.error(
            "Engine MISSING root=%s err=%s — запустіть: bash pack.sh && задеплойте runtime/",
            root,
            exc,
        )
    return root


def engine_status() -> dict:
    return {
        "ready": _READY,
        "error": _ENGINE_ERROR,
        "root": str(_ROOT) if _ROOT else None,
        "has_runtime": (Path(__file__).resolve().parent.parent / "runtime" / "cosmyparser").is_dir(),
    }


def require_engine() -> None:
    if not _READY:
        setup()
    if not _READY:
        raise RuntimeError(
            _ENGINE_ERROR
            or "cosmy_parse недоступний — на сервері потрібен runtime/ (bash pack.sh)"
        )
