"""Хешування паролів для WebUser (bcrypt напряму, без passlib)."""

import bcrypt


def _password_bytes_for_bcrypt(password: str, max_bytes: int = 72) -> bytes:
    data = password.encode("utf-8")
    return data[:max_bytes] if len(data) > max_bytes else data


def bcrypt_hash(password: str) -> str:
    pw = _password_bytes_for_bcrypt(password)
    return bcrypt.hashpw(pw, bcrypt.gensalt()).decode("ascii")


def bcrypt_verify(password: str, password_hash: str | None) -> bool:
    if not password_hash or not isinstance(password_hash, str):
        return False
    pw = _password_bytes_for_bcrypt(password)
    h = password_hash.encode("utf-8")
    try:
        return bcrypt.checkpw(pw, h)
    except (ValueError, TypeError):
        return False
