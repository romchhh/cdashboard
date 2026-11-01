import asyncio
from unittest.mock import MagicMock, patch

import config
import novapay.auth as auth
from novapay.api_auth import api_auth_kwargs


def test_api_auth_kwargs_uses_jwt_only():
    assert api_auth_kwargs("tok123") == {"principal": "", "jwt": "tok123"}
    assert api_auth_kwargs("  x  ")["jwt"] == "x"


def test_credentials_for_fop_requires_real_refresh():
    config.NOVAPAY_TOKENS = {
        "Test": {
            "refresh_token": "session-managed",
            "certificate": "pem",
            "login": "api_user",
        }
    }
    assert auth.credentials_for_fop("Test") is None

    config.NOVAPAY_TOKENS = {
        "Test": {
            "refresh_token": "rt-abc",
            "certificate": "pem",
            "login": "api_user",
        }
    }
    creds = auth.credentials_for_fop("Test")
    assert creds["refresh_token"] == "rt-abc"
    assert creds["login"] == "api_user"


def test_cyrillic_fops_get_separate_refresh_stash(tmp_path, monkeypatch):
    """Кириличні імена більше не згортаються в один _.refresh.json."""
    import novapay.token_store as ts

    monkeypatch.setattr(ts, "SESSIONS_DIR", str(tmp_path))
    monkeypatch.setattr(config, "SESSIONS_DIR", str(tmp_path))
    monkeypatch.setattr(config, "SESSION_SUFFIX", {"Єльникова": "y", "Салтицька": "s"})
    monkeypatch.setattr(
        config,
        "ensure_session_suffix",
        lambda name: config.SESSION_SUFFIX.get((name or "").strip(), "x"),
    )

    ts.stash_rotated_tokens("Єльникова", "rt-y", "cert-y", login="YelnikovaAPI")
    ts.stash_rotated_tokens("Салтицька", "rt-s", "cert-s", login="SaltytskaAPI")

    assert (tmp_path / "novapay_refresh_y.json").is_file()
    assert (tmp_path / "novapay_refresh_s.json").is_file()
    assert not (tmp_path / "_.refresh.json").is_file()

    config.NOVAPAY_TOKENS = {
        "Єльникова": {"refresh_token": "db-y", "certificate": "db-cert-y", "login": "YelnikovaAPI"},
        "Салтицька": {"refresh_token": "db-s", "certificate": "db-cert-s", "login": "SaltytskaAPI"},
    }
    with patch.object(ts, "SESSIONS_DIR", str(tmp_path)):
        cy = auth.credentials_for_fop("Єльникова")
        cs = auth.credentials_for_fop("Салтицька")
    assert cy["refresh_token"] == "rt-y"
    assert cy["login"] == "YelnikovaAPI"
    assert cs["refresh_token"] == "rt-s"
    assert cs["login"] == "SaltytskaAPI"


def test_stash_with_other_login_does_not_override(tmp_path, monkeypatch):
    import novapay.token_store as ts

    monkeypatch.setattr(ts, "SESSIONS_DIR", str(tmp_path))
    monkeypatch.setattr(config, "SESSIONS_DIR", str(tmp_path))
    monkeypatch.setattr(config, "SESSION_SUFFIX", {"Тимченко": "t"})
    monkeypatch.setattr(config, "ensure_session_suffix", lambda name: "t")

    # Імітація старого бага: у stash чужий логін
    (tmp_path / "novapay_refresh_t.json").write_text(
        '{"fop_name":"Тимченко","refresh_token":"foreign-rt","public_certificate":"foreign-cert","login":"PylypchakHD"}',
        encoding="utf-8",
    )
    config.NOVAPAY_TOKENS = {
        "Тимченко": {
            "refresh_token": "own-rt",
            "certificate": "own-cert",
            "login": "TymchenkoNV",
        }
    }
    with patch.object(ts, "SESSIONS_DIR", str(tmp_path)):
        creds = auth.credentials_for_fop("Тимченко")
    assert creds["refresh_token"] == "own-rt"
    assert creds["login"] == "TymchenkoNV"


def test_parse_novapay_expiration_without_seconds():
    parsed = auth._parse_api_expiration("21.10.2026 16:25")
    assert parsed == __import__("datetime").datetime(2026, 10, 21, 16, 25)


def test_short_jwt_is_not_refreshed_immediately():
    from datetime import datetime, timedelta

    now = datetime(2026, 10, 21, 14, 26)
    # Щойно виданий JWT живе ~2 години — не ротуємо refresh на кожному синку.
    assert auth.session_needs_refresh(now + timedelta(hours=2), now=now) is False
    assert auth.session_needs_refresh(now + timedelta(minutes=90), now=now) is False
    assert auth.session_needs_refresh(now + timedelta(minutes=20), now=now) is True
    assert auth.session_needs_refresh(now + timedelta(hours=10), now=now) is False


def test_save_session_keeps_real_novapay_expiration(tmp_path):
    import json

    path = tmp_path / "sess.json"
    auth.save_session("not-a-jwt", "21.10.2026 16:25", session_file=str(path))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["expiration"] == "2026-10-21 16:25:00"


def test_load_session_prefers_earlier_jwt_exp(tmp_path):
    import base64
    import json
    from datetime import datetime, timedelta, timezone

    exp = datetime.now(timezone.utc) - timedelta(hours=1)
    payload = base64.urlsafe_b64encode(
        json.dumps({"aud": "Api", "exp": int(exp.timestamp())}).encode()
    ).decode().rstrip("=")
    token = f"h.{payload}.s"
    path = tmp_path / "sess.json"
    path.write_text(
        json.dumps(
            {
                "jwt": token,
                "principal": token,
                "expiration": "2099-01-01 00:00:00",
            }
        ),
        encoding="utf-8",
    )
    assert auth.load_session(session_file=str(path)) == (None, None)


def test_jwt_audience_and_login_match():
    # header.payload.sig — payload={"aud":"TymchenkoNV"}
    import base64
    import json

    payload = base64.urlsafe_b64encode(json.dumps({"aud": "TymchenkoNV"}).encode()).decode().rstrip("=")
    token = f"x.{payload}.y"
    assert auth.jwt_audience(token) == "TymchenkoNV"
    assert auth.jwt_matches_login(token, "TymchenkoNV")
    assert not auth.jwt_matches_login(token, "PylypchakHD")


def test_ensure_jwt_session_keeps_file_session_without_login(tmp_path):
    """Без логіна API не чіпаємо валідну сесію з файлу."""
    config.NOVAPAY_TOKENS = {
        "Єльникова": {
            "refresh_token": "rt",
            "certificate": "cert",
            "login": "",
        }
    }
    session = tmp_path / "sess.json"
    session.write_text(
        '{"jwt":"keep-me","principal":"keep-me","expiration":"2099-01-01 00:00:00"}',
        encoding="utf-8",
    )
    with patch("novapay.token_store.purge_legacy_shared_stash", return_value=False), \
         patch("novapay.token_store.load_rotated_stash", return_value=None):
        assert auth.ensure_jwt_session("Єльникова", session_file=str(session)) == "keep-me"


def test_ensure_jwt_session_rejects_aud_mismatch_when_login_known(tmp_path):
    import base64
    import json

    payload = base64.urlsafe_b64encode(json.dumps({"aud": "OtherLogin"}).encode()).decode().rstrip("=")
    foreign = f"x.{payload}.y"
    config.NOVAPAY_TOKENS = {
        "Тимченко": {
            "refresh_token": "rt",
            "certificate": "cert",
            "login": "TymchenkoNV",
        }
    }
    session = tmp_path / "sess.json"
    session.write_text(
        json.dumps(
            {"jwt": foreign, "principal": foreign, "expiration": "2099-01-01 00:00:00"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with patch("novapay.token_store.purge_legacy_shared_stash", return_value=False):
        with patch.object(
            auth,
            "_renew_jwt_for_fop_impl",
            return_value={"ok": True, "jwt": "fresh-for-tymchenko"},
        ) as renew:
            out = auth.ensure_jwt_session("Тимченко", session_file=str(session))
    assert out == "fresh-for-tymchenko"
    renew.assert_called_once()


def test_jwt_authenticate_credentials_validation_without_soap():
    result = auth.jwt_authenticate_credentials("", "login", "cert")
    assert result["ok"] is False
    assert "Refresh Token" in result["error"]

    result = auth.jwt_authenticate_credentials("rt", "", "cert")
    assert result["ok"] is False
    assert "логін" in result["error"].casefold()

    result = auth.jwt_authenticate_credentials("rt", "login", "")
    assert result["ok"] is False
    assert "certificate" in result["error"].casefold()


def test_jwt_authenticate_credentials_success_mocked(tmp_path):
    resp = MagicMock()
    resp.result = "ok"
    resp.jwt = "jwt-token-abc"
    resp.refresh_token = "new-refresh"
    resp.public_certificate = "new-cert"
    resp.expiration = "2030-01-01 12:00:00"

    client = MagicMock()
    client.get_type.return_value = lambda **kwargs: kwargs
    client.service.UserAuthenticationJWT.return_value = resp

    session_file = tmp_path / "sess.json"

    with patch("novapay.token_store.SESSIONS_DIR", str(tmp_path)):
        with patch.object(auth, "create_client", return_value=client):
            with patch.object(auth, "persist_novapay_tokens_sync") as persist:
                result = auth.jwt_authenticate_credentials(
                    "old-refresh",
                    "api_login",
                    "pem-cert",
                    session_file=str(session_file),
                    fop_name="TestFop",
                    persist_tokens=True,
                )

    assert result["ok"] is True
    assert result["jwt"] == "jwt-token-abc"
    persist.assert_called_once_with(
        "TestFop",
        "new-refresh",
        "new-cert",
        login="api_login",
    )
    assert session_file.is_file()
    import json

    data = json.loads(session_file.read_text(encoding="utf-8"))
    assert data["jwt"] == "jwt-token-abc"
    assert data.get("auth_mode") == "jwt"


def test_jwt_keeps_rotated_refresh_when_database_write_fails(tmp_path):
    """Новий refresh одноразовий: якщо БД не збереглась, наступний виклик не має слати старий токен."""
    resp = MagicMock()
    resp.result = "ok"
    resp.jwt = "jwt-new"
    resp.refresh_token = "rotated-refresh"
    resp.public_certificate = "rotated-cert"
    resp.expiration = "2030-01-01 12:00:00"
    client = MagicMock()
    client.get_type.return_value = lambda **kwargs: kwargs
    client.service.UserAuthenticationJWT.return_value = resp

    config.NOVAPAY_TOKENS = {
        "FopZ": {
            "refresh_token": "old-refresh",
            "certificate": "old-cert",
            "login": "api_login",
        }
    }
    with patch("novapay.token_store.SESSIONS_DIR", str(tmp_path)):
        with patch.object(auth, "create_client", return_value=client):
            with patch.object(auth, "persist_novapay_tokens_sync", side_effect=RuntimeError("db down")):
                result = auth.jwt_authenticate_credentials(
                    "old-refresh",
                    "api_login",
                    "old-cert",
                    session_file=str(tmp_path / "sess.json"),
                    fop_name="FopZ",
                    persist_tokens=True,
                )
        assert result["ok"] is True
        assert result["refresh_token"] == "rotated-refresh"
        assert result.get("warning")
        creds = auth.credentials_for_fop("FopZ")
    assert creds["refresh_token"] == "rotated-refresh"
    assert creds["public_certificate"] == "rotated-cert"


def test_start_novapay_session_requires_login_in_credentials():
    config.NOVAPAY_TOKENS = {
        "FopY": {
            "refresh_token": "rt",
            "certificate": "cert",
            "login": "",
        }
    }
    from web.fop_admin import start_novapay_session

    out = start_novapay_session("FopY")
    assert out["ok"] is False
    assert "логін" in (out.get("error") or "").casefold()


def test_persist_novapay_tokens_from_async_loop():
    """Після JWT persist не повинен падати з greenlet_spawn у FastAPI."""
    from novapay.token_store import persist_novapay_tokens_sync

    async def _run():
        persist_novapay_tokens_sync(
            "__no_such_fop__",
            "r",
            "c",
            login="x",
        )

    asyncio.run(_run())


def test_renew_jwt_for_fop_delegates_to_credentials(tmp_path):
    config.NOVAPAY_TOKENS = {
        "FopX": {
            "refresh_token": "rt",
            "certificate": "cert",
            "login": "stored_login",
        }
    }
    with patch.object(
        auth,
        "jwt_authenticate_credentials",
        return_value={"ok": True, "jwt": "j"},
    ) as jwt_auth:
        out = auth.renew_jwt_for_fop("FopX", session_file=str(tmp_path / "s.json"))
    assert out["ok"] is True
    jwt_auth.assert_called_once()
    args, kwargs = jwt_auth.call_args
    assert args[0] == "rt"
    assert args[1] == "stored_login"
    assert kwargs["fop_name"] == "FopX"
