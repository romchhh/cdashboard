from __future__ import annotations

from fastapi import Header, HTTPException, status

from . import config


def require_api_key(x_parser_key: str | None = Header(default=None, alias="X-Parser-Key")) -> None:
    if not config.REQUIRE_API_KEY:
        return
    if not config.API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PARSER_API_KEY не задано на worker-і",
        )
    if not x_parser_key or x_parser_key.strip() != config.API_KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid parser API key")
